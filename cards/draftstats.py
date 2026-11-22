"""La note d'une draft, d'après les parties pro importées.

Même méthode que les outils de draft qui calculent des chances de victoire
à partir des données (le modèle de DraftGap, documenté) : tout se passe en
« logits » sur l'échelle Elo (400 points = facteur 10), où 0 vaut 50 %.

- Chaque champion à son poste a une note de base : son bilan l'année de la
  partie, lissé vers son bilan de toutes les années (PRIOR_BASE parties).
- Chaque paire de champions face à face (les 25 paires, pas seulement le
  même couloir) corrige : bilan réel de la paire, lissé vers ce que leurs
  notes de base prédisaient (PRIOR_PAIR parties), moins cette prédiction.
- Chaque paire d'alliés (synergie) corrige de la même façon.

total = bases(A) + synergies(A) + face-à-face(A contre B) − bases(B) − synergies(B)
chances de A = W(total)

Les requêtes sont groupées et gardées en cache : une par champion déjà
choisi (ses face-à-face, ses synergies), une pour toutes les notes de base.
"""
from __future__ import annotations

import hashlib
import math

from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Q

from esport.models import GameAppearance

#: Parties fictives de lissage (les données pro sont bien plus rares que
#: celles des parties classées : DraftGap en met 1 000 par défaut).
PRIOR_BASE = 60
PRIOR_PAIR = 25
#: Le meilleur bilan admis (évite les logits infinis).
CLAMP = 0.02
CACHE_SECONDS = 24 * 3600


def L(p: float) -> float:
    """Chances → logit (échelle Elo)."""
    p = min(1 - CLAMP, max(CLAMP, p))
    return -400 * math.log10(1 / p - 1)


def W(x: float) -> float:
    """Logit → chances."""
    return 1 / (1 + 10 ** (-x / 400))


# --------------------------------------------------------------------- Données
def base_table() -> dict:
    """{(champion, poste): {année: [parties, victoires], "all": [...]}}."""
    table = cache.get("draft:base")
    if table is not None:
        return table
    table = {}
    rows = (GameAppearance.objects.exclude(champion="").order_by()
            .values_list("champion", "role", "tournament__split__year")
            .annotate(n=Count("id"), w=Count("id", filter=Q(win=True))))
    for champion, role, year, n, w in rows:
        entry = table.setdefault((champion, role), {"all": [0, 0]})
        entry["all"][0] += n
        entry["all"][1] += w
        if year:
            y = entry.setdefault(year, [0, 0])
            y[0] += n
            y[1] += w
    cache.set("draft:base", table, CACHE_SECONDS)
    return table


def _pairs(champion: str, role: str, same_team: bool) -> dict:
    """{(champion, poste): [parties, victoires de ce champion]} face à (ou avec) champion/poste."""
    key = "draft:%s:%s" % ("syn" if same_team else "vs",
                           hashlib.md5(f"{champion}|{role}".encode()).hexdigest())
    found = cache.get(key)
    if found is not None:
        return found
    table = GameAppearance._meta.db_table
    relation = "a.win = b.win AND a.player_id <> b.player_id" if same_team else "a.win <> b.win"
    sql = (
        f"SELECT a.champion, a.role, COUNT(*), SUM(CASE WHEN a.win THEN 1 ELSE 0 END) "
        f"FROM {table} a JOIN {table} b ON a.game_id = b.game_id AND {relation} "
        f"WHERE b.champion = %s AND b.role = %s AND a.champion <> '' GROUP BY a.champion, a.role"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, [champion, role])
        found = {(c, r): [int(n), int(w or 0)] for c, r, n, w in cursor.fetchall()}
    cache.set(key, found, CACHE_SECONDS)
    return found


def vs(champion: str, role: str) -> dict:
    return _pairs(champion, role, same_team=False)


def with_(champion: str, role: str) -> dict:
    return _pairs(champion, role, same_team=True)


# --------------------------------------------------------------------- Notes
def base_rate(champion: str, role: str, year: int | None, table: dict | None = None) -> float:
    """Le bilan lissé d'un champion à son poste, l'année donnée (chances)."""
    entry = (table or base_table()).get((champion, role))
    if not entry:
        return 0.5
    games, wins = entry["all"]
    overall = (wins + PRIOR_BASE * 0.5) / (games + PRIOR_BASE)
    g, w = entry.get(year, [0, 0]) if year else entry["all"]
    return (w + PRIOR_BASE * overall) / (g + PRIOR_BASE)


def matchup_correction(a: tuple, b: tuple, rate_a: float, rate_b: float) -> tuple[float, int, float]:
    """(correction, parties, bilan observé de a) pour a=(champion, poste) face à b."""
    f = L(rate_a) - L(rate_b)
    games, wins = vs(*b).get(a, [0, 0])
    obs = (wins + PRIOR_PAIR * W(f)) / (games + PRIOR_PAIR)
    return L(obs) - f, games, obs


def synergy_correction(a: tuple, b: tuple, rate_a: float, rate_b: float) -> tuple[float, int]:
    g = L(rate_a) + L(rate_b)
    games, wins = with_(*b).get(a, [0, 0])
    obs = (wins + PRIOR_PAIR * W(g)) / (games + PRIOR_PAIR)
    return L(obs) - g, games


def contributions(team: dict[str, str], enemy: dict[str, str], year: int | None = None) -> dict:
    """Ce que chaque champion de `team` apporte, en logits :
    {poste: {"rate" (bilan de base, 0–1), "base", "synergy", "matchup", "lane_games", "lane_rate"}}.
    La synergie d'une paire est partagée entre ses deux champions."""
    table = base_table()
    rates = {r: base_rate(c, r, year, table) for r, c in team.items()}
    enemy_rates = {r: base_rate(c, r, year, table) for r, c in enemy.items()}
    out = {}
    for ra, name in team.items():
        a = (name, ra)
        synergy = sum(synergy_correction(a, (team[rb], rb), rates[ra], rates[rb])[0]
                      for rb in team if rb != ra) / 2
        match, lane_games, lane_rate = 0.0, 0, 0.5
        for rb, other in enemy.items():
            correction, games, obs = matchup_correction(a, (other, rb), rates[ra], enemy_rates[rb])
            match += correction
            if rb == ra:
                lane_games, lane_rate = games, obs
        out[ra] = {"rate": rates[ra], "base": L(rates[ra]), "synergy": synergy, "matchup": match,
                   "lane_games": lane_games, "lane_rate": lane_rate}
    return out


def evaluate(team_a: dict[str, str], team_b: dict[str, str], year: int | None = None) -> dict:
    """team_* : {poste: nom de champion}. Chaque côté est calculé de son
    point de vue (comme DraftGap), puis les deux chances sont ramenées à
    100 % ensemble. Renvoie {"rate_a", "rate_b", "a", "b"} (détail par poste)."""
    a = contributions(team_a, team_b, year)
    b = contributions(team_b, team_a, year)

    def own(side):
        return sum(d["base"] + d["synergy"] for d in side.values())

    total_a = own(a) + sum(d["matchup"] for d in a.values()) - own(b)
    total_b = own(b) + sum(d["matchup"] for d in b.values()) - own(a)
    raw_a, raw_b = W(total_a), W(total_b)
    rate_a = raw_a / (raw_a + raw_b) if raw_a + raw_b else 0.5
    return {"rate_a": rate_a, "rate_b": 1 - rate_a, "a": a, "b": b}


#: Les points de poste d'un champion : ses chances seul (base + sa part des
#: synergies + ses face-à-face), à 60 % il apporte +DRAFT_WEIGHT/10 points.
DRAFT_WEIGHT = 40


def slot_points(detail: dict) -> float:
    return round((W(detail["base"] + detail["synergy"] + detail["matchup"]) - 0.5) * DRAFT_WEIGHT, 1)


def latest_year() -> int | None:
    years = [y for entry in base_table().values() for y in entry if y != "all"]
    return max(years) if years else None

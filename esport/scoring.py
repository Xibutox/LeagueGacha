"""Conversion des valeurs Leaguepedia + agrégation des stats + note de performance.

Fonctions pures (sans Django) pour pouvoir les tester facilement.

La note d'un joueur sur un split est calculée **par rapport aux joueurs du même
rôle** (un support n'a pas le même DPM qu'un mid), puis transformée en
percentile sur l'ensemble du split. C'est ce percentile qui détermine la rareté.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime, timezone

# --------------------------------------------------------------------------
# Conversion des valeurs renvoyées par Cargo (toujours des chaînes)
# --------------------------------------------------------------------------


def parse_int(value, default=0):
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def parse_float(value, default=None):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def parse_bool(value) -> bool:
    return str(value).strip().lower() in {"yes", "1", "true", "y"}


def parse_date(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
    # Leaguepedia met parfois des dates « inconnues » (0000-00-00, 1900-01-01…).
    return parsed if parsed.year > 1950 else None


def parse_datetime_utc(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


ROLE_ALIASES = {
    "top": "Top",
    "jungle": "Jungle",
    "jungler": "Jungle",
    "mid": "Mid",
    "middle": "Mid",
    "bot": "Bot",
    "adc": "Bot",
    "bottom": "Bot",
    "support": "Support",
    "sup": "Support",
}


def normalize_role(value) -> str:
    return ROLE_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip())


# --------------------------------------------------------------------------
# Agrégation
# --------------------------------------------------------------------------


def _div(a, b):
    return a / b if b else 0.0


def aggregate_appearances(appearances: list[dict]) -> dict:
    """Agrège une liste de lignes de scoreboard d'UN joueur.

    Clés attendues par ligne : kills, deaths, assists, gold, cs, damage,
    vision_score (peut être None), team_kills, team_gold, team_damage,
    game_length (minutes, peut être None), win, role, team, champion.
    """
    games = len(appearances)
    kills = sum(a["kills"] for a in appearances)
    deaths = sum(a["deaths"] for a in appearances)
    assists = sum(a["assists"] for a in appearances)
    wins = sum(1 for a in appearances if a["win"])

    timed = [a for a in appearances if a.get("game_length")]
    minutes = sum(a["game_length"] for a in timed)
    vision_rows = [a for a in timed if a.get("vision_score") is not None]
    vision_minutes = sum(a["game_length"] for a in vision_rows)

    role = Counter(a["role"] for a in appearances if a.get("role")).most_common(1)
    team = Counter(a["team"] for a in appearances if a.get("team")).most_common(1)
    champions = Counter(a["champion"] for a in appearances if a.get("champion")).most_common()

    return {
        "games": games,
        "wins": wins,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "win_rate": round(100 * _div(wins, games), 1),
        "kda": round(_div(kills + assists, max(deaths, 1)), 2),
        "kill_participation": round(
            100 * _div(sum(a["kills"] + a["assists"] for a in appearances), sum(a["team_kills"] for a in appearances)),
            1,
        ),
        "cs_per_min": round(_div(sum(a["cs"] for a in timed), minutes), 2),
        "gold_per_min": round(_div(sum(a["gold"] for a in timed), minutes), 1),
        "damage_per_min": round(_div(sum(a["damage"] for a in timed), minutes), 1),
        "vision_per_min": round(_div(sum(a["vision_score"] for a in vision_rows), vision_minutes), 2),
        "damage_share": round(
            100 * _div(sum(a["damage"] for a in appearances), sum(a["team_damage"] for a in appearances)), 1
        ),
        "gold_share": round(
            100 * _div(sum(a["gold"] for a in appearances), sum(a["team_gold"] for a in appearances)), 1
        ),
        "role": role[0][0] if role else "",
        "team": team[0][0] if team else None,
        "champions": [{"name": name, "games": count} for name, count in champions],
    }


# --------------------------------------------------------------------------
# Note de performance
# --------------------------------------------------------------------------
#
# Inspirée des notes par partie des sites de stats pros (ProView, Sheep
# Stats…) : chaque partie reçoit une note de 0 à 100 où 50 est la partie
# moyenne d'un pro **du même rôle**. Pour chaque métrique, on mesure de
# combien d'écarts-types le joueur est au-dessus ou en dessous de la
# moyenne de son rôle (sur toutes les parties importées), on pondère selon
# le rôle, puis on ramène sur 0–100. La note d'un split est la moyenne de
# ses parties, tirée vers 50 quand il y en a peu (moyenne bayésienne).

#: (libellé, plus c'est haut mieux c'est ?)
METRICS = {
    "kda": ("KDA", True),
    "kp": ("Participation aux kills", True),
    "deaths_pm": ("Morts / min", False),
    "dpm": ("Dégâts / min", True),
    "dmg_share": ("Part des dégâts", True),
    "dmg_per_gold": ("Dégâts par gold", True),
    "cspm": ("CS / min", True),
    "gpm": ("Gold / min", True),
    "vspm": ("Vision / min", True),
    "win": ("Victoire", True),
}

#: Les dimensions affichées sur la fiche : moyenne de leurs métriques.
DIMENSIONS = {
    "combat": ("Combat", ("kda", "deaths_pm")),
    "impact": ("Impact", ("kp",)),
    "degats": ("Dégâts", ("dpm", "dmg_share", "dmg_per_gold")),
    "farm": ("Farm", ("cspm", "gpm")),
    "vision": ("Vision", ("vspm",)),
    "victoire": ("Victoire", ("win",)),
}

#: Poids des métriques par rôle (somme = 1). À ajuster librement.
ROLE_WEIGHTS = {
    "Top": {"kda": .15, "kp": .08, "deaths_pm": .10, "dpm": .10, "dmg_share": .12, "dmg_per_gold": .05,
            "cspm": .12, "gpm": .08, "vspm": .05, "win": .15},
    "Jungle": {"kda": .15, "kp": .18, "deaths_pm": .10, "dpm": .05, "dmg_share": .07, "dmg_per_gold": .05,
               "cspm": .05, "gpm": .05, "vspm": .12, "win": .18},
    "Mid": {"kda": .15, "kp": .10, "deaths_pm": .10, "dpm": .12, "dmg_share": .12, "dmg_per_gold": .06,
            "cspm": .10, "gpm": .05, "vspm": .05, "win": .15},
    "Bot": {"kda": .15, "kp": .08, "deaths_pm": .10, "dpm": .14, "dmg_share": .14, "dmg_per_gold": .06,
            "cspm": .10, "gpm": .08, "win": .15},
    "Support": {"kda": .15, "kp": .22, "deaths_pm": .10, "dpm": .04, "dmg_share": .04,
                "vspm": .27, "win": .18},
}
DEFAULT_WEIGHTS = ROLE_WEIGHTS["Mid"]

#: Parties « moyennes » (note 50) ajoutées à celles du joueur dans la note
#: d'un split : 3 parties à 60 pèsent moitié moins que 30 parties à 60.
PRIOR_GAMES = 3
#: Un z-score est borné pour qu'une partie folle ne fasse pas toute la note.
Z_CAP = 3.0
#: Pente de la courbe z → note (1 écart-type ≈ +19 points).
SLOPE = 0.8


def game_metrics(row: dict) -> dict:
    """Les métriques d'une partie (ligne de scoreboard). None quand la
    donnée manque (durée ou vision inconnues)."""
    minutes = row.get("game_length") or None
    metrics = {
        "kda": (row["kills"] + row["assists"]) / max(row["deaths"], 1),
        "kp": _div(row["kills"] + row["assists"], row["team_kills"]) if row["team_kills"] else None,
        "dmg_share": _div(row["damage"], row["team_damage"]) if row["team_damage"] else None,
        "dmg_per_gold": _div(row["damage"], row["gold"]) if row["gold"] else None,
        "win": 1.0 if row["win"] else 0.0,
        "deaths_pm": None,
        "dpm": None,
        "cspm": None,
        "gpm": None,
        "vspm": None,
    }
    if minutes:
        metrics.update(
            deaths_pm=row["deaths"] / minutes,
            dpm=row["damage"] / minutes,
            cspm=row["cs"] / minutes,
            gpm=row["gold"] / minutes,
            vspm=(row["vision_score"] / minutes) if row.get("vision_score") is not None else None,
        )
    # Le KDA explose avec 0 mort : on l'écrase en log.
    metrics["kda"] = math.log1p(metrics["kda"])
    return metrics


def reference(rows: list[dict]) -> dict:
    """Moyenne et écart-type de chaque métrique, par rôle : la « partie
    moyenne d'un pro » à laquelle chaque partie est comparée."""
    values: dict = {}
    for row in rows:
        role = row.get("role") or "?"
        for metric, value in game_metrics(row).items():
            if value is not None:
                values.setdefault(role, {}).setdefault(metric, []).append(value)
    ref = {}
    for role, metrics in values.items():
        ref[role] = {}
        for metric, vals in metrics.items():
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            ref[role][metric] = (mean, math.sqrt(var))
    return ref


def _to_score(z: float) -> float:
    return 100 / (1 + math.exp(-SLOPE * z))


def game_rating(row: dict, ref: dict) -> tuple[float, dict]:
    """(note 0–100 de la partie, {métrique: z-score})."""
    role = row.get("role") or "?"
    weights = ROLE_WEIGHTS.get(role, DEFAULT_WEIGHTS)
    role_ref = ref.get(role, {})
    zs = {}
    total = weight_sum = 0.0
    for metric, value in game_metrics(row).items():
        if value is None or metric not in role_ref:
            continue
        mean, std = role_ref[metric]
        z = 0.0 if std == 0 else (value - mean) / std
        z = max(-Z_CAP, min(Z_CAP, z))
        if not METRICS[metric][1]:
            z = -z
        zs[metric] = z
        w = weights.get(metric, 0)
        total += w * z
        weight_sum += w
    z_game = total / weight_sum if weight_sum else 0.0
    return round(_to_score(z_game), 1), zs


#: La moyenne de nombreuses parties varie bien moins qu'une partie seule :
#: on étire l'écart à 50 pour que les notes de split couvrent 0–100 (sur la
#: LCK 2026, de ~15 pour le moins bon à ~80 pour le meilleur).
SPLIT_STRETCH = 2.5

#: La version du calcul des notes. À augmenter quand il change : au
#: démarrage suivant, tout est recalculé (recompute_cards --si-besoin).
#: 2 : prestige des ligues. 3 : prestige plus marqué, internationaux sans correction.
SCORING_VERSION = 3


def split_rating(game_scores: list[float], prior_games: int = PRIOR_GAMES, prior_mean: float = 50.0) -> float:
    """Moyenne bayésienne des parties (tirée vers prior_mean — le joueur
    moyen de la ligue — quand il y en a peu), étirée autour de 50."""
    mean = (sum(game_scores) + prior_mean * prior_games) / (len(game_scores) + prior_games)
    return round(max(0.0, min(100.0, 50 + SPLIT_STRETCH * (mean - 50))), 1)


def dimension_scores(game_zs: list[dict]) -> dict:
    """{dimension: note 0–100} : la moyenne des z-scores de la dimension sur
    les parties, ramenée sur 0–100."""
    result = {}
    for key, (_, metrics) in DIMENSIONS.items():
        zs = [z for g in game_zs for m, z in g.items() if m in metrics]
        if zs:
            result[key] = round(_to_score(sum(zs) / len(zs)))
    return result


def _midrank_percentile(value: float, population: list[float]) -> float:
    """Percentile « mid-rank » (0–100) de value dans population (qui contient value)."""
    n = len(population)
    if n <= 1:
        return 50.0
    below = sum(1 for v in population if v < value)
    equal = sum(1 for v in population if v == value) - 1
    return 100.0 * (below + 0.5 * equal) / (n - 1)


def rank_percentiles(stats: list[dict], key: str = "rating") -> list[dict]:
    """Ajoute `percentile` (rang 0–100 dans le split) d'après `key`."""
    population = [s[key] for s in stats]
    for s in stats:
        s["percentile"] = round(_midrank_percentile(s[key], population), 2)
    return stats


def overall_rating(rating: float) -> int:
    """Note affichée sur la carte, de 50 à 99, d'après la note 0–100.
    Un pro moyen (50) a 75 ; un 90 a 94."""
    return max(50, min(99, round(50 + 0.49 * rating)))


# --------------------------------------------------------------------------
# Champions et équipes
# --------------------------------------------------------------------------


#: Poids d'une partie de l'historique (périodes précédentes) face à une
#: partie de la période en cours, dans la note sur un champion.
HISTORY_WEIGHT = 0.5

#: Un champion jamais joué en compétition : la note du joueur, moins ceci.
UNPLAYED_PENALTY = 10.0


def champion_rating(
    game_scores: list[float],
    player_mean: float,
    prior_games: int = PRIOR_GAMES,
    history_scores: list[float] | None = None,
    history_weight: float = HISTORY_WEIGHT,
) -> float:
    """Note d'un joueur sur un champion (0–100), sur la même échelle que la
    note du split.

    La forme du moment (ses parties sur ce champion pendant la période)
    compte pleinement ; son historique sur ce champion (périodes
    précédentes) compte à `history_weight`. Le tout est tiré vers sa
    moyenne de la période (et non vers 50) quand il y a peu de parties :
    2 parties moyennes cette saison mais 5 très bonnes avant, et la note
    reste bonne."""
    history_scores = history_scores or []
    total = sum(game_scores) + history_weight * sum(history_scores) + player_mean * prior_games
    weight = len(game_scores) + history_weight * len(history_scores) + prior_games
    mean = total / weight
    return round(max(0.0, min(100.0, 50 + SPLIT_STRETCH * (mean - 50))), 1)


def unplayed_rating(player_rating: float) -> float:
    return round(max(0.0, player_rating - UNPLAYED_PENALTY), 1)


#: Dans le score d'un poste, la part de la note du joueur sur la période ;
#: le reste vient de sa note sur le champion choisi.
SLOT_PLAYER_WEIGHT = 0.5

#: Dans le score d'une composition, la part de sa cohérence ; le reste
#: vient de la moyenne de ses 5 postes.
COHERENCE_WEIGHT = 0.25


def slot_score(player_rating: float, champion_rating_value: float, bonus_pct: float = 0) -> float:
    """Score d'un poste : note du joueur et note sur le champion, plus le
    bonus de rareté de l'exemplaire (en %)."""
    base = SLOT_PLAYER_WEIGHT * player_rating + (1 - SLOT_PLAYER_WEIGHT) * champion_rating_value
    return round(base * (1 + float(bonus_pct) / 100), 1)


def team_score(slot_scores: list[float]) -> float:
    """Moyenne de scores (postes d'une composition, ou compositions d'une équipe)."""
    return round(sum(slot_scores) / len(slot_scores), 1) if slot_scores else 0.0


def composition_score(slot_scores: list[float], coherence: float) -> float:
    return round(
        (1 - COHERENCE_WEIGHT) * team_score(slot_scores) + COHERENCE_WEIGHT * coherence, 1
    )


# --------------------------------------------------------------------------
# Cohérence d'une composition
# --------------------------------------------------------------------------
#: Les classes de Data Dragon.
FRONTLINE_TAGS = {"Tank"}
CARRY_TAGS = {"Marksman", "Mage", "Assassin"}
UTILITY_TAGS = {"Support", "Tank"}


def _is_known(p: dict) -> bool:
    return bool(p.get("tags")) or (p.get("attack", 0) + p.get("magic", 0)) > 0


def _magic_share(p: dict) -> float:
    a, m = p.get("attack", 0), p.get("magic", 0)
    return m / (a + m) if a + m else 0.5


def _is_frontline(p: dict) -> bool:
    tags = set(p.get("tags") or [])
    return bool(tags & FRONTLINE_TAGS) or ("Fighter" in tags and p.get("defense", 0) >= 5)


def _check(label: str, points: float, maximum: float, detail: str) -> dict:
    points = round(max(0.0, min(maximum, points)), 1)
    state = "ok" if points >= 0.8 * maximum else ("warn" if points > 0 else "bad")
    return {"label": label, "points": points, "max": maximum, "detail": detail, "state": state}


def composition_coherence(profiles: list[dict], duos_seen: int = 0) -> dict:
    """La cohérence d'une composition de 5 champions, sur 100.

    profiles : un dict par champion (Champion.profile) : classes Data
    Dragon et profil attaque / défense / magie (0–10).
    duos_seen : paires de ces champions déjà jouées ensemble, dans la même
    équipe, en compétition.

    Cinq critères : dégâts mixtes, ligne de front, sources de dégâts,
    contrôle et protection, classes variées ; plus un bonus pour les duos
    éprouvés. Renvoie {"score", "checks": [{label, points, max, detail, state}]}."""
    known = [p for p in profiles if _is_known(p)]
    checks = []

    # 1. Dégâts physiques et magiques : idéal entre 30 et 70 % de magie.
    share = sum(_magic_share(p) for p in known) / len(known) if known else 0.5
    if share < 0.3:
        pts = 25 * share / 0.3
    elif share > 0.7:
        pts = 25 * (1 - share) / 0.3
    else:
        pts = 25
    checks.append(_check(
        "Dégâts variés", pts, 25,
        f"{round(100 * (1 - share))} % physiques, {round(100 * share)} % magiques",
    ))

    # 2. Ligne de front : un ou deux champions solides.
    front = sum(1 for p in known if _is_frontline(p))
    pts = {0: 0, 1: 18, 2: 25, 3: 20}.get(front, 10)
    checks.append(_check(
        "Ligne de front", pts, 25,
        "aucun champion solide" if front == 0 else f"{front} champion{'s' if front > 1 else ''} solide{'s' if front > 1 else ''}",
    ))

    # 3. Sources de dégâts : tireur, mage ou assassin de classe principale
    # (Bard, « Support » puis « Mage », n'en est pas un).
    carries = sum(1 for p in known if (p.get("tags") or [""])[0] in CARRY_TAGS)
    pts = {0: 0, 1: 12, 2: 20, 3: 20}.get(carries, 10)
    checks.append(_check(
        "Sources de dégâts", pts, 20,
        "personne pour faire les dégâts" if carries == 0 else f"{carries} carry{'s' if carries > 1 else ''}",
    ))

    # 4. Contrôle et protection : un support ou un tank.
    utility = sum(1 for p in known if set(p.get("tags") or []) & UTILITY_TAGS)
    checks.append(_check(
        "Contrôle et protection", 15 if utility else 0, 15,
        "un support ou un tank" if utility else "ni support ni tank",
    ))

    # 5. Classes variées : pas plus de deux champions de la même classe principale.
    primaries: dict[str, int] = {}
    for p in known:
        if p.get("tags"):
            primaries[p["tags"][0]] = primaries.get(p["tags"][0], 0) + 1
    most = max(primaries.values(), default=0)
    pts = 15 if most <= 2 else (7 if most == 3 else 0)
    crowded = max(primaries, key=primaries.get) if primaries else ""
    checks.append(_check(
        "Classes variées", pts, 15,
        f"{most} × {crowded}" if most > 2 else f"{len(primaries)} classes",
    ))

    base = sum(c["points"] for c in checks)
    bonus = min(10, 2.5 * duos_seen)
    if duos_seen:
        checks.append({
            "label": "Duos éprouvés", "points": bonus, "max": 10, "state": "ok",
            "detail": f"{duos_seen} duo{'s' if duos_seen > 1 else ''} déjà joué{'s' if duos_seen > 1 else ''} en compétition",
        })
    unknown = len(profiles) - len(known)
    score = min(100.0, base + bonus)
    # Un champion sans profil (pas encore synchronisé) compte pour neutre.
    if unknown and known:
        score = score * len(known) / len(profiles) + 60 * unknown / len(profiles)
    elif not known:
        score = 60.0
    return {"score": round(score, 1), "checks": checks, "unknown": unknown}

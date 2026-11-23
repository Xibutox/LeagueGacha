"""L'international fictif de la Fantasy : pendant l'intersaison, les
équipes du dernier split des 4 majeures s'affrontent en ronde suisse,
2 Bo3 par équipe et par semaine.

- Le vainqueur d'une partie est tiré selon la force des deux équipes (la
  note moyenne de leurs 5 joueurs, sur l'échelle Elo) ;
- la ligne de stats de chaque joueur est une de ses vraies parties des
  2 dernières années, de même résultat, les plus récentes plus probables.
  Ses points restent donc entre sa pire et sa meilleure partie réelle.

Une graine par semaine : les mêmes résultats pour tout le monde.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import timedelta

from django.db.models import Count

from esport.models import GameAppearance

from .models import ROLES, Card, FantasyCompetition, FantasySimMatch, FantasyWeek

#: 10 points de note d'écart ≈ 70 % de chances de gagner une partie.
ELO_PER_POINT = 15
MIN_GAMES = 10
HISTORY_DAYS = 730
HALF_LIFE_DAYS = 180
#: Jours des deux rondes de la semaine (décalage depuis le mercredi).
ROUND_DAYS = [[0, 1, 2], [3, 4]]


def win_prob(rating_a: float, rating_b: float) -> float:
    return 1 / (1 + 10 ** (-(rating_a - rating_b) * ELO_PER_POINT / 400))


def setup_teams(comp: FantasyCompetition) -> int:
    """Les équipes du dernier split de chaque ligue majeure, avec pour
    chaque poste le joueur le plus aligné (au moins MIN_GAMES parties
    réelles en tout)."""
    from .fantasy import latest_major_splits, major_key

    splits = latest_major_splits()
    rows = (GameAppearance.objects.filter(tournament__split__in=splits, team__isnull=False)
            .values("team_id", "team__name", "team__short_name", "tournament__split__league__short_name",
                    "tournament__split__league__name", "player_id", "role")
            .annotate(n=Count("id")))
    totals = dict(GameAppearance.objects.filter(player_id__in={r["player_id"] for r in rows})
                  .values_list("player_id").annotate(n=Count("id")))
    teams = {}
    for r in rows:
        if r["role"] not in ROLES or totals.get(r["player_id"], 0) < MIN_GAMES:
            continue
        t = teams.setdefault(r["team_id"], {
            "id": r["team_id"], "name": r["team__name"], "short": r["team__short_name"] or "",
            "league": r["tournament__split__league__short_name"] or r["tournament__split__league__name"],
            "candidates": defaultdict(Counter)})
        t["candidates"][r["role"]][r["player_id"]] += r["n"]
    used = set()
    out = []
    for t in sorted(teams.values(), key=lambda t: t["name"]):
        players = {}
        for role in ROLES:
            for pid, _ in t["candidates"][role].most_common():
                if pid not in used:
                    players[role] = pid
                    used.add(pid)
                    break
        if sum(1 for p in players.values() if p) < 4:
            continue
        out.append({"id": t["id"], "name": t["name"], "short": t["short"], "league": t["league"],
                    "players": players})
    ratings = {}
    for pid, overall in (Card.objects.filter(player_id__in=used).order_by("player_id", "-split__year", "-overall")
                         .values_list("player_id", "overall")):
        ratings.setdefault(pid, overall)
    for t in out:
        notes = [ratings[p] for p in t["players"].values() if p in ratings]
        t["rating"] = round(sum(notes) / len(notes), 1) if notes else 70.0
    comp.state = {"teams": out, "records": {}, "met": [], "splits": [s.name for s in splits]}
    comp.save(update_fields=["state"])
    return len(out)


class Sampler:
    """Tire une vraie partie d'un joueur, de même résultat."""

    def __init__(self, player_ids, reference, rng: random.Random):
        self.rng = rng
        self.games = defaultdict(lambda: {True: [], False: []})
        since = reference - timedelta(days=HISTORY_DAYS)
        qs = GameAppearance.objects.filter(player_id__in=list(player_ids)).values_list(
            "pk", "player_id", "win", "played_at")
        for pk, pid, win, played in qs.order_by("pk"):
            if played is not None and not (since <= played.date() <= reference):
                continue
            age = (reference - played.date()).days if played else HALF_LIFE_DAYS
            self.games[pid][bool(win)].append((pk, 0.5 ** (age / HALF_LIFE_DAYS)))

    def draw(self, pid: int, win: bool) -> int | None:
        pool = self.games[pid][win] or self.games[pid][not win]
        if not pool:
            return None
        return self.rng.choices([p for p, _ in pool], weights=[w for _, w in pool])[0]


def _pairs(teams, records, met, rng):
    order = sorted(teams, key=lambda t: (-(records.get(str(t["id"]), [0, 0])[0] - records.get(str(t["id"]), [0, 0])[1]),
                                         rng.random()))
    pairs = []
    while len(order) >= 2:
        a = order.pop(0)
        index = next((i for i, b in enumerate(order) if tuple(sorted((a["id"], b["id"]))) not in met), 0)
        pairs.append((a, order.pop(index)))
    return pairs


def simulate_week(week: FantasyWeek) -> int:
    """Tire les matchs de la semaine (2 rondes suisses). Renvoie leur nombre."""
    comp = week.competition
    teams = comp.state.get("teams") or []
    if len(teams) < 2 or week.matches.exists():
        return 0
    rng = random.Random(f"{comp.slug}:{week.start.isoformat()}")
    records = comp.state.setdefault("records", {})
    met = {tuple(p) for p in comp.state.get("met", [])}
    sampler = Sampler({p for t in teams for p in t["players"].values() if p}, week.start, rng)
    made = 0
    for offsets in ROUND_DAYS:
        for i, (a, b) in enumerate(_pairs(teams, records, met, rng)):
            p = win_prob(a["rating"], b["rating"])
            wins = {"a": 0, "b": 0}
            games = []
            while max(wins.values()) < 2:
                winner = "a" if rng.random() < p else "b"
                wins[winner] += 1
                lines = {}
                for side, team in (("a", a), ("b", b)):
                    for pid in team["players"].values():
                        if pid:
                            aid = sampler.draw(pid, winner == side)
                            if aid:
                                lines[str(pid)] = aid
                games.append({"winner": winner, "lines": lines})
            FantasySimMatch.objects.create(
                week=week, day=week.start + timedelta(days=offsets[i % len(offsets)]),
                team_a={k: a[k] for k in ("id", "name", "short", "players")},
                team_b={k: b[k] for k in ("id", "name", "short", "players")},
                wins_a=wins["a"], wins_b=wins["b"], games=games)
            win_a = wins["a"] > wins["b"]
            for team, won in ((a, win_a), (b, not win_a)):
                rec = records.setdefault(str(team["id"]), [0, 0])
                rec[0 if won else 1] += 1
            met.add(tuple(sorted((a["id"], b["id"]))))
            made += 1
    comp.state["met"] = [list(p) for p in sorted(met)]
    comp.save(update_fields=["state"])
    return made


def standings(comp: FantasyCompetition, now=None) -> list[dict]:
    """Le classement des équipes fictives, d'après les résultats dévoilés."""
    from .fantasy import revealed_day

    records = defaultdict(lambda: [0, 0])
    for week in comp.weeks.all():
        for m in week.matches.filter(day__lte=revealed_day(week, now)):
            winner, loser = (m.team_a, m.team_b) if m.wins_a > m.wins_b else (m.team_b, m.team_a)
            records[winner["id"]][0] += 1
            records[loser["id"]][1] += 1
    rows = [{**t, "wins": records[t["id"]][0], "losses": records[t["id"]][1]} for t in comp.state.get("teams", [])]
    return sorted(rows, key=lambda r: (-(r["wins"] - r["losses"]), -r["wins"], r["name"]))

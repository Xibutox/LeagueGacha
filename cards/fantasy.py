"""La Fantasy League : des joueurs pros de sa collection, des points selon
leurs vraies parties de la semaine.

- Une compétition par ligue majeure (le split en cours), une par tournoi
  international de Riot, et un international fictif aux matchs simulés
  (cards/fantasy_sim.py) quand rien ne se joue.
- Semaine du mercredi 00:00 au mardi 23:59 (heure de Paris) : l'équipe se
  compose jusqu'au mardi soir, se verrouille au début de la semaine.
- Équipe : 5 joueurs (un par poste), dont 1 capitaine (×1,5),
  dans un plafond de coût ; bonus de diversité selon le nombre d'équipes
  réelles.
- Points d'une partie : barème d'actions (kill +3, assist +2, mort −1…)
  + bonus de surperformance (la moitié de ce que le joueur fait de mieux
  que sa moyenne des 10 dernières parties, au plus +10).
"""
from __future__ import annotations

import itertools
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import F, Q, Window
from django.db.models.functions import RowNumber
from django.utils import timezone
from django.utils.text import slugify

from esport.catalog import MAJOR_LEAGUES, major_leagues_q, riot_events_q
from esport.models import GameAppearance, League, Split, Tournament

from .models import (
    ROLES,
    Booster,
    FantasyCompetition,
    FantasyLineup,
    FantasyPlayerWeek,
    FantasyWeek,
    UserCard,
)
from .services import GameError

# --------------------------------------------------------------------- Barème
KILL, ASSIST, DEATH = 3, 2, -1
CS_PER_POINT = 100
VISION_PER_POINT = 25
BIG_GAME = 10  # 10 kills ou 10 assists : +2
BIG_GAME_POINTS = 2
WIN = 2
FAST_WIN = 2
FAST_WIN_MINUTES = 30
#: Surperformance : la moitié de l'écart avec la moyenne habituelle, au plus +10.
OVER_SHARE = 0.5
OVER_MAX = 10.0
#: La moyenne habituelle (et le coût) : les 10 dernières parties.
REFERENCE_GAMES = 10
MIN_REFERENCE_GAMES = 5
DEFAULT_COST = 20.0
CAPTAIN = 1.5
#: Plafond : 5 joueurs × la moyenne de la compétition × 1,1.
CAP_PLAYERS = 5
CAP_MARGIN = 1.1
#: Diversité : +5 % par équipe réelle en plus de la première, au plus +20 %.
DIVERSITY_STEP = 5
DIVERSITY_MAX = 20
#: Paliers : part du score de l'équipe idéale → (palier, boosters).
TIERS = [(0.65, "or", 3), (0.50, "argent", 2), (0.35, "bronze", 1)]
TIER_LABELS = {"or": "Or", "argent": "Argent", "bronze": "Bronze"}
SEASON_PRIZES = [5, 4, 3]
#: Les matchs simulés d'un jour sont dévoilés à cette heure.
REVEAL_HOUR = 18
MAJORS = list(MAJOR_LEAGUES)  # LEC, LCK, LPL, LCS
ORDER = ["LCK", "LPL", "LEC", "LCS"]


def game_points(line: dict) -> tuple[float, list[tuple[str, float]]]:
    """Les points d'actions d'une partie et leur détail.
    line : {kills, deaths, assists, cs, vision, win, length}."""
    kills, deaths, assists = line.get("kills") or 0, line.get("deaths") or 0, line.get("assists") or 0
    parts = [
        (f"{kills} kill{'s' if kills > 1 else ''}", kills * KILL),
        (f"{assists} assist{'s' if assists > 1 else ''}", assists * ASSIST),
        (f"{deaths} mort{'s' if deaths > 1 else ''}", deaths * DEATH),
    ]
    if line.get("cs"):
        parts.append((f"{line['cs']} CS", line["cs"] / CS_PER_POINT))
    if line.get("vision"):
        parts.append((f"{line['vision']} de vision", line["vision"] / VISION_PER_POINT))
    if kills >= BIG_GAME or assists >= BIG_GAME:
        parts.append(("10 kills ou assists", BIG_GAME_POINTS))
    if line.get("win"):
        parts.append(("victoire", WIN))
        if line.get("length") and line["length"] < FAST_WIN_MINUTES:
            parts.append((f"victoire en moins de {FAST_WIN_MINUTES} min", FAST_WIN))
    parts = [(label, round(v, 2)) for label, v in parts]
    return round(sum(v for _, v in parts), 1), parts


def overperformance(points: float, reference: float | None) -> float:
    if reference is None:
        return 0.0
    return round(min(OVER_MAX, max(0.0, OVER_SHARE * (points - reference))), 1)


def appearance_line(a) -> dict:
    """Une GameAppearance (ou ses values()) en ligne de stats."""
    get = a.get if isinstance(a, dict) else (lambda k, d=None: getattr(a, k, d))
    return {
        "kills": get("kills") or 0, "deaths": get("deaths") or 0, "assists": get("assists") or 0,
        "cs": get("cs"), "vision": get("vision_score"), "win": bool(get("win")), "length": get("game_length"),
    }


# --------------------------------------------------------------------- Semaines
def wednesday(d: date) -> date:
    """Le mercredi qui ouvre la semaine fantasy de ce jour."""
    return d - timedelta(days=(d.weekday() - 2) % 7)


def today(now: datetime | None = None) -> date:
    return timezone.localtime(now or timezone.now()).date()


def at(d: date) -> datetime:
    """Ce jour à 00:00, heure de Paris."""
    return timezone.make_aware(datetime.combine(d, time.min))


def lock_time(week: FantasyWeek) -> datetime:
    return at(week.start)


def week_end(week: FantasyWeek) -> datetime:
    return at(week.start + timedelta(days=7))


def is_open(week: FantasyWeek, now: datetime | None = None) -> bool:
    return (now or timezone.now()) < lock_time(week)


# --------------------------------------------------------------------- Compétitions
def major_key(league: League | None) -> str | None:
    if league is None:
        return None
    names = {(league.short_name or "").upper(), (league.name or "").upper()}
    for key, aliases in MAJOR_LEAGUES.items():
        if names & {a.upper() for a in aliases}:
            return key
    return None


def _ensure(slug: str, **fields) -> FantasyCompetition:
    tournaments = fields.pop("tournaments", [])
    comp, created = FantasyCompetition.objects.get_or_create(slug=slug, defaults=fields)
    if not created:
        changed = [k for k in ("start", "end", "name") if k in fields and getattr(comp, k) != fields[k]]
        for k in changed:
            setattr(comp, k, fields[k])
        if changed:
            comp.save(update_fields=changed)
    if tournaments:
        comp.tournaments.add(*tournaments)
    return comp


def sync_competitions(now: datetime | None = None) -> list[FantasyCompetition]:
    """Crée ou met à jour les compétitions d'après les dates des tournois
    importés : le split en cours ou qui commence dans les 3 semaines de
    chaque ligue majeure, les tournois internationaux, et l'international
    fictif quand rien de réel ne se joue cette semaine ni la suivante."""
    day = today(now)
    horizon = day + timedelta(days=21)
    dated = Tournament.objects.filter(start_date__isnull=False, end_date__isnull=False,
                                      start_date__lte=horizon, end_date__gte=day - timedelta(days=7))
    comps = []
    splits = defaultdict(list)
    for t in dated.filter(major_leagues_q_for_tournament()).select_related("league", "split"):
        if t.split_id:
            splits[t.split].append(t)
    for split, tournaments in splits.items():
        key = major_key(split.league)
        if not key:
            continue
        label = split.split_label or split.name
        comps.append(_ensure(
            slugify(f"{key}-{split.year}-{label}")[:200], kind=FantasyCompetition.Kind.LEAGUE,
            name=f"{key} {split.year} {label}".strip(), league=split.league, split=split,
            start=wednesday(min(t.start_date for t in tournaments)), end=max(t.end_date for t in tournaments),
            tournaments=tournaments,
        ))
    events = defaultdict(list)
    for t in dated.filter(riot_events_q_for_tournament()).select_related("league"):
        events[(t.league, t.year or t.start_date.year)].append(t)
    for (league, year), tournaments in events.items():
        name = f"{league.short_name or league.name} {year}"
        comps.append(_ensure(
            slugify(name)[:200], kind=FantasyCompetition.Kind.INTERNATIONAL, name=name, league=league,
            start=wednesday(min(t.start_date for t in tournaments)), end=max(t.end_date for t in tournaments),
            tournaments=tournaments,
        ))
    # L'international fictif : seulement si aucune compétition réelle ne couvre
    # cette semaine ni la suivante.
    this_week = wednesday(day)
    real = FantasyCompetition.objects.exclude(kind=FantasyCompetition.Kind.FICTIONAL)
    busy = real.filter(start__lte=this_week + timedelta(days=13), end__gte=this_week).exists()
    if not busy:
        upcoming = real.filter(start__gt=this_week).order_by("start").first()
        limit = upcoming.start - timedelta(days=1) if upcoming else this_week + timedelta(days=13)
        fictional = (FantasyCompetition.objects.filter(kind=FantasyCompetition.Kind.FICTIONAL,
                                                       end__gte=this_week - timedelta(days=1)).first())
        if fictional is None:
            from . import fantasy_sim

            fictional = FantasyCompetition.objects.create(
                kind=FantasyCompetition.Kind.FICTIONAL, name=f"International fictif {this_week:%Y-%m}",
                slug=slugify(f"international-fictif-{this_week:%Y-%m-%d}"),
                start=this_week, end=min(this_week + timedelta(days=13), limit),
            )
            fantasy_sim.setup_teams(fictional)
        elif fictional.end < min(this_week + timedelta(days=13), limit):
            fictional.end = min(this_week + timedelta(days=13), limit)  # tant que rien ne commence
            fictional.save(update_fields=["end"])
        comps.append(fictional)
    return comps


def major_leagues_q_for_tournament() -> Q:
    return Q(league__in=League.objects.filter(major_leagues_q()))


def riot_events_q_for_tournament() -> Q:
    return Q(league__in=League.objects.filter(riot_events_q()))


def active_competitions(now: datetime | None = None) -> list[FantasyCompetition]:
    """Celles qu'on peut jouer : en cours ou qui commencent, dans l'ordre
    LCK, LPL, LEC, LCS, International, puis le fictif."""
    day = today(now)
    comps = list(FantasyCompetition.objects.filter(end__gte=day - timedelta(days=6)).select_related("league"))

    def order(c):
        if c.kind == FantasyCompetition.Kind.LEAGUE:
            key = major_key(c.league)
            return (0, ORDER.index(key) if key in ORDER else 9, c.start)
        return (1 if c.kind == FantasyCompetition.Kind.INTERNATIONAL else 2, 0, c.start)

    return sorted(comps, key=order)


def tab_label(comp: FantasyCompetition) -> str:
    if comp.kind == FantasyCompetition.Kind.LEAGUE:
        return major_key(comp.league) or comp.name
    return "International" if comp.kind == FantasyCompetition.Kind.INTERNATIONAL else "International fictif"


# --------------------------------------------------------------------- Joueurs éligibles
def _latest_by_player(rows) -> dict[int, dict]:
    out = {}
    for pid, role, team in rows:
        if pid not in out and role in ROLES:
            out[pid] = {"role": role, "team": team or ""}
    return out


def competition_pool(comp: FantasyCompetition) -> dict[int, dict]:
    """{id du joueur: {role, team}} : ceux qui peuvent marquer dans la compétition."""
    if comp.kind == FantasyCompetition.Kind.FICTIONAL:
        return {pid: {"role": role, "team": t["short"] or t["name"]}
                for t in comp.state.get("teams", []) for role, pid in t["players"].items() if pid}
    order = ("-played_at", "-tournament__start_date", "-pk")
    qs = GameAppearance.objects.filter(tournament__in=comp.tournaments.all())
    current = _latest_by_player(qs.order_by(*order).values_list("player_id", "role", "team__short_name"))
    # Plus ceux qui n'ont pas encore joué : le split précédent de la ligue,
    # ou pour un international le dernier split des 4 majeures (seulement
    # avant les premiers matchs : ensuite, seuls ceux qui jouent comptent).
    if current and comp.kind == FantasyCompetition.Kind.INTERNATIONAL:
        return current
    if comp.kind == FantasyCompetition.Kind.LEAGUE and comp.league_id:
        prev = (Split.objects.filter(league=comp.league, tournaments__appearances__isnull=False)
                .exclude(pk=comp.split_id).order_by("-year", "-start_date", "-pk").distinct().first())
        splits = [prev] if prev else []
    else:
        splits = latest_major_splits()
    qs = GameAppearance.objects.filter(tournament__split__in=splits)
    pool = _latest_by_player(qs.order_by(*order).values_list("player_id", "role", "team__short_name"))
    pool.update(current)
    return pool


def latest_major_splits() -> list[Split]:
    out = []
    for league in League.objects.filter(major_leagues_q()):
        split = (Split.objects.filter(league=league, tournaments__appearances__isnull=False)
                 .order_by("-year", "-start_date", "-pk").distinct().first())
        if split:
            out.append(split)
    # Une ligue sous plusieurs noms : on garde le split le plus récent par clé.
    best = {}
    for s in out:
        key = major_key(s.league) or s.league_id
        if key not in best or (s.year, s.pk) > (best[key].year, best[key].pk):
            best[key] = s
    return list(best.values())


def owned_players(user) -> set[int]:
    return set(UserCard.objects.filter(user=user, quantity__gt=0).values_list("card__player_id", flat=True))


# --------------------------------------------------------------------- Coûts
def reference_points(player_ids, before: datetime) -> dict[int, tuple[float, int]]:
    """{id: (moyenne de points des 10 dernières parties avant `before`, parties)}."""
    if not player_ids:
        return {}
    qs = (GameAppearance.objects.filter(player_id__in=list(player_ids))
          .filter(Q(played_at__lt=before) | Q(played_at__isnull=True))
          .annotate(rank=Window(RowNumber(), partition_by=[F("player_id")],
                                order_by=[F("played_at").desc(nulls_last=True), F("pk").desc()]))
          .filter(rank__lte=REFERENCE_GAMES)
          .values("player_id", "kills", "deaths", "assists", "cs", "vision_score", "win", "game_length"))
    sums = defaultdict(list)
    for row in qs:
        sums[row["player_id"]].append(game_points(appearance_line(row))[0])
    return {pid: (round(sum(v) / len(v), 1), len(v)) for pid, v in sums.items()}


def prepare_week(week: FantasyWeek, pool: dict | None = None, refresh: bool = True) -> None:
    """Crée (ou met à jour, tant que la semaine est ouverte) les joueurs de
    la semaine avec leur coût, et le plafond."""
    pool = competition_pool(week.competition) if pool is None else pool
    refs = reference_points(pool.keys(), lock_time(week))
    by_role = defaultdict(list)
    for pid, (avg, n) in refs.items():
        if n >= MIN_REFERENCE_GAMES and pid in pool:
            by_role[pool[pid]["role"]].append(avg)
    role_avg = {r: round(sum(v) / len(v), 1) if v else DEFAULT_COST for r, v in by_role.items()}
    existing = {pw.player_id: pw for pw in week.players.all()}
    costs = []
    for pid, info in pool.items():
        avg, n = refs.get(pid, (None, 0))
        cost = avg if n >= MIN_REFERENCE_GAMES else role_avg.get(info["role"], DEFAULT_COST)
        costs.append(cost)
        pw = existing.get(pid)
        if pw is None:
            FantasyPlayerWeek.objects.create(week=week, player_id=pid, role=info["role"], team=info["team"], cost=cost)
        elif refresh and week.status == FantasyWeek.Status.OPEN and (pw.cost, pw.role, pw.team) != (
                cost, info["role"], info["team"]):
            pw.cost, pw.role, pw.team = cost, info["role"], info["team"]
            pw.save(update_fields=["cost", "role", "team"])
    if costs and (week.status == FantasyWeek.Status.OPEN or not week.cap):
        week.cap = round(CAP_PLAYERS * (sum(costs) / len(costs)) * CAP_MARGIN, 1)
        week.save(update_fields=["cap"])


def get_week(comp: FantasyCompetition, start: date) -> FantasyWeek | None:
    """La semaine de la compétition qui commence ce mercredi (créée au besoin)."""
    if start < comp.start or start > comp.end:
        return None
    number = (start - comp.start).days // 7 + 1
    week, created = FantasyWeek.objects.get_or_create(competition=comp, start=start, defaults={"number": number})
    if created:
        prepare_week(week)
    return week


def current_week(comp: FantasyCompetition, now: datetime | None = None) -> FantasyWeek | None:
    """La semaine qui se joue (verrouillée)."""
    return get_week(comp, wednesday(today(now)))


def editable_week(comp: FantasyCompetition, now: datetime | None = None) -> FantasyWeek | None:
    """La prochaine semaine à composer : celle qui commence au prochain mercredi."""
    start = wednesday(today(now)) + timedelta(days=7)
    return get_week(comp, max(start, comp.start))


# --------------------------------------------------------------------- Équipe
def players_of_week(week: FantasyWeek) -> dict[int, FantasyPlayerWeek]:
    return {pw.player_id: pw for pw in week.players.select_related("player")}


def save_lineup(user, week: FantasyWeek, picks: dict, captain: str, now: datetime | None = None) -> FantasyLineup:
    """picks : {poste: id}."""
    if not is_open(week, now):
        raise GameError("Cette semaine est verrouillée : ton équipe vaudra pour la suivante.")
    pool = players_of_week(week)
    owned = owned_players(user)
    chosen = {}
    for slot in ROLES:
        raw = picks.get(slot)
        if not raw:
            raise GameError("Il faut un joueur à chaque poste.")
        pid = int(raw)
        pw = pool.get(pid)
        if pw is None:
            raise GameError("Un des joueurs ne joue pas dans cette compétition.")
        if pid not in owned:
            raise GameError(f"Tu n'as pas de carte de {pw.player.ign}.")
        if slot in ROLES and pw.role != slot:
            raise GameError(f"{pw.player.ign} joue {pw.role}, pas {slot}.")
        chosen[slot] = pid
    if len(set(chosen.values())) != len(chosen):
        raise GameError("Un joueur ne peut occuper qu'une place.")
    if captain not in ROLES:
        raise GameError("Choisis un capitaine parmi tes titulaires.")
    cost = round(sum(pool[pid].cost for pid in chosen.values()), 1)
    if week.cap and cost > week.cap + 1e-6:
        raise GameError(f"Ton équipe coûte {cost:g}, au-dessus du plafond de {week.cap:g}.")
    lineup, _ = FantasyLineup.objects.update_or_create(
        user=user, week=week, defaults={"players": {k: v for k, v in chosen.items()}, "captain": captain,
                                        "cost": cost})
    return lineup


def ensure_lineups(week: FantasyWeek) -> int:
    """Reconduction : chacun reprend son équipe de la semaine précédente."""
    prev = FantasyWeek.objects.filter(competition=week.competition, start__lt=week.start).order_by("-start").first()
    if prev is None:
        return 0
    pool = players_of_week(week)
    have = set(week.lineups.values_list("user_id", flat=True))
    made = 0
    for old in prev.lineups.exclude(user_id__in=have):
        players = {k: v for k, v in old.players.items() if v in pool}
        FantasyLineup.objects.create(user_id=old.user_id, week=week, players=players, captain=old.captain,
                                     cost=round(sum(pool[v].cost for v in players.values()), 1))
        made += 1
    return made


def diversity_bonus(teams: list[str]) -> int:
    distinct = {t for t in teams if t}
    return min(DIVERSITY_MAX, DIVERSITY_STEP * max(0, len(distinct) - 1))


def lineup_score(players: dict, captain: str, pws: dict[int, FantasyPlayerWeek]) -> tuple[float, dict]:
    """Le score d'une équipe et son détail : le capitaine compte ×1,5,
    plus le bonus de diversité."""
    slots = []
    for role in ROLES:
        pw = pws.get(players.get(role))
        entry = {"role": role, "player": players.get(role), "points": pw.points if pw is not None else 0.0}
        entry["team"] = pw.team if pw is not None else ""
        if role == captain:
            entry["captain"] = True
            entry["points"] = round(entry["points"] * CAPTAIN, 1)
        slots.append(entry)
    complete = all(players.get(r) for r in ROLES)
    div = diversity_bonus([s["team"] for s in slots if s["player"]]) if complete else 0
    base = sum(s["points"] for s in slots)
    total = round(base * (1 + div / 100), 1)
    return total, {"slots": slots, "diversity": div, "base": round(base, 1)}


# --------------------------------------------------------------------- Points
def revealed_day(week: FantasyWeek, until: datetime | None = None) -> date:
    """International fictif : le dernier jour dont les résultats sont
    dévoilés (ceux d'un jour le sont à REVEAL_HOUR)."""
    until = until or timezone.now()
    if until >= week_end(week):
        return week.start + timedelta(days=6)
    local = timezone.localtime(until)
    return local.date() if local.hour >= REVEAL_HOUR else local.date() - timedelta(days=1)


def week_games(week: FantasyWeek, until: datetime | None = None) -> dict[int, list[dict]]:
    """{id du joueur: [partie]} : les parties de la semaine jouées (ou, pour
    l'international fictif, dévoilées) avant `until`."""
    until = min(until or timezone.now(), week_end(week))
    out = defaultdict(list)
    fields = ("pk", "player_id", "kills", "deaths", "assists", "cs", "vision_score", "win", "game_length",
              "champion", "opponent", "played_at", "role", "team__short_name")
    if week.competition.kind == FantasyCompetition.Kind.FICTIONAL:
        matches = list(week.matches.filter(day__lte=revealed_day(week, until)))
        ids = {aid for m in matches for g in m.games for aid in g["lines"].values()}
        rows = {r["pk"]: r for r in GameAppearance.objects.filter(pk__in=ids).values(*fields)}
        for m in matches:
            for n, g in enumerate(m.games, 1):
                for pid, aid in g["lines"].items():
                    row = rows.get(aid)
                    if row is None:
                        continue
                    pid = int(pid)
                    side_a = pid in m.team_a["players"].values()
                    opponent = (m.team_b if side_a else m.team_a)
                    out[pid].append({**appearance_line(row), "date": m.day.isoformat(), "game": n,
                                     "champion": row["champion"], "opponent": opponent["short"] or opponent["name"],
                                     "match": m.pk})
        return out
    qs = GameAppearance.objects.filter(tournament__in=week.competition.tournaments.all(),
                                       played_at__gte=lock_time(week), played_at__lt=until)
    for row in qs.order_by("played_at").values(*fields):
        out[row["player_id"]].append({**appearance_line(row), "date": timezone.localtime(row["played_at"]).date()
                                      .isoformat(), "champion": row["champion"], "opponent": row["opponent"],
                                      "role": row["role"], "team": row["team__short_name"] or ""})
    return out


def score_week(week: FantasyWeek, until: datetime | None = None, final: bool = False) -> None:
    """Met à jour les points des joueurs et des équipes de la semaine."""
    games = week_games(week, until)
    pws = players_of_week(week)
    for pid, lines in games.items():
        if pid not in pws:  # un joueur apparu en cours de route
            first = lines[0]
            pws[pid] = FantasyPlayerWeek.objects.create(
                week=week, player_id=pid, role=first.get("role") or "", team=first.get("team") or "",
                cost=DEFAULT_COST)
    for pid, pw in pws.items():
        detail, total = [], 0.0
        for line in games.get(pid, []):
            base, parts = game_points(line)
            bonus = overperformance(base, pw.cost)
            detail.append({**line, "base": base, "bonus": bonus, "total": round(base + bonus, 1), "parts": parts})
            total += base + bonus
        total = round(total, 1)
        if detail != pw.games or total != pw.points:
            pw.games, pw.points = detail, total
            pw.save(update_fields=["games", "points"])
    for lineup in week.lineups.all():
        points, detail = lineup_score(lineup.players, lineup.captain, pws)
        lineup.points, lineup.detail = points, detail
        lineup.save(update_fields=["points", "detail"])
    if final:
        ideal, score = ideal_team(week, pws)
        week.ideal, week.ideal_score = ideal, score
        week.status = FantasyWeek.Status.SCORED
        week.scored_at = timezone.now()
        week.save(update_fields=["ideal", "ideal_score", "status", "scored_at"])
        for lineup in week.lineups.all():
            lineup.tier = tier_for(lineup, score)
            lineup.save(update_fields=["tier"])


def ideal_team(week: FantasyWeek, pws: dict[int, FantasyPlayerWeek]) -> tuple[dict, float]:
    """Les meilleurs titulaires de la semaine dans le plafond, avec le
    meilleur en capitaine et le bonus de diversité."""
    cap5 = week.cap or math.inf
    best_by_role = {r: sorted((pw for pw in pws.values() if pw.role == r), key=lambda p: -p.points)[:8]
                    for r in ROLES}
    if not all(best_by_role.values()):
        return {}, 0.0
    best, best_score = None, -1.0
    for combo in itertools.product(*(best_by_role[r] for r in ROLES)):
        if sum(p.cost for p in combo) > cap5 + 1e-6:
            continue
        base = sum(p.points for p in combo) + (CAPTAIN - 1) * max(p.points for p in combo)
        score = base * (1 + diversity_bonus([p.team for p in combo]) / 100)
        if score > best_score:
            best, best_score = combo, score
    if best is None:
        return {}, 0.0
    return {r: p.player_id for r, p in zip(ROLES, best)}, round(best_score, 1)


def tier_for(lineup: FantasyLineup, ideal_score: float) -> str:
    if ideal_score <= 0 or not all(lineup.players.get(r) for r in ROLES):
        return ""
    ratio = lineup.points / ideal_score
    return next((name for share, name, _ in TIERS if ratio >= share), "")


# --------------------------------------------------------------------- Récompenses
def booster_for(comp: FantasyCompetition) -> Booster | None:
    from .services import BASE_BOOSTER_SLUG

    if comp.kind == FantasyCompetition.Kind.LEAGUE and comp.league_id:
        year = comp.split.year if comp.split_id else comp.start.year
        for booster in Booster.objects.filter(is_active=True, leagues=comp.league).order_by("order"):
            if not booster.years or year in booster.year_list():
                return booster
    return Booster.objects.filter(slug=BASE_BOOSTER_SLUG).first() or Booster.objects.filter(is_active=True).first()


def reward_week(week: FantasyWeek) -> int:
    from .services import give_boosters

    booster = booster_for(week.competition)
    if booster is None:
        return 0
    given = 0
    quantities = {name: n for _, name, n in TIERS}
    for lineup in week.lineups.exclude(tier="").select_related("user"):
        give_boosters([lineup.user], booster, quantities[lineup.tier], message=(
            f"Fantasy {tab_label(week.competition)}, semaine {week.number} : palier {TIER_LABELS[lineup.tier]}"))
        given += 1
    return given


def season_ranking(comp: FantasyCompetition) -> list[dict]:
    totals = defaultdict(float)
    names = {}
    for lineup in FantasyLineup.objects.filter(week__competition=comp).select_related("user"):
        totals[lineup.user_id] += lineup.points
        names[lineup.user_id] = lineup.user
    rows = sorted(({"user": names[u], "points": round(p, 1)} for u, p in totals.items()), key=lambda r: -r["points"])
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def reward_season(comp: FantasyCompetition) -> int:
    from .services import give_boosters

    booster = booster_for(comp)
    if booster is None or comp.rewarded:
        return 0
    rows = [r for r in season_ranking(comp) if r["points"] > 0]
    for row, quantity in zip(rows, SEASON_PRIZES):
        give_boosters([row["user"]], booster, quantity,
                      message=f"Fantasy {comp.name} : {row['rank']}e du classement final")
    comp.rewarded = True
    comp.save(update_fields=["rewarded"])
    return min(len(rows), len(SEASON_PRIZES))


# --------------------------------------------------------------------- Le tour
def advance(comp: FantasyCompetition, now: datetime | None = None) -> dict:
    """Fait avancer une compétition : semaine à composer préparée,
    verrouillage et reconduction, matchs simulés, points, comptage
    définitif, récompenses de la semaine et de la saison."""
    from . import fantasy_sim

    now = now or timezone.now()
    day = today(now)
    stats = defaultdict(int)
    current_week(comp, now)
    upcoming = editable_week(comp, now)
    if upcoming is not None and upcoming.status == FantasyWeek.Status.OPEN:
        prepare_week(upcoming)
    pending = comp.weeks.filter(start__lte=day).exclude(status=FantasyWeek.Status.SCORED).order_by("start")
    for week_id in pending.values_list("pk", flat=True):
        with transaction.atomic():
            week = FantasyWeek.objects.select_for_update().get(pk=week_id)
            if week.status == FantasyWeek.Status.SCORED:
                continue
            if week.status == FantasyWeek.Status.OPEN:
                stats["reconduites"] += ensure_lineups(week)
                week.status = FantasyWeek.Status.LOCKED
                week.save(update_fields=["status"])
            if comp.kind == FantasyCompetition.Kind.FICTIONAL and not week.matches.exists():
                stats["matchs simulés"] += fantasy_sim.simulate_week(week)
            final = week_end(week) <= now
            score_week(week, now, final=final)
            if final:
                stats["récompenses"] += reward_week(week)
                stats["semaines comptées"] += 1
    if comp.end < day and not comp.rewarded and not comp.weeks.exclude(status=FantasyWeek.Status.SCORED).exists():
        with transaction.atomic():
            locked = FantasyCompetition.objects.select_for_update().get(pk=comp.pk)
            stats["podiums"] += reward_season(locked)
    return dict(stats)


def tick(now: datetime | None = None, log=lambda m: None) -> dict:
    """La tâche planifiée : compétitions d'après les tournois importés, puis
    chacune avance."""
    now = now or timezone.now()
    sync_competitions(now)
    total = defaultdict(int)
    for comp in FantasyCompetition.objects.filter(end__gte=today(now) - timedelta(days=14)):
        for key, n in advance(comp, now).items():
            total[key] += n
        log(f"{comp.name} : à jour")
    return dict(total)

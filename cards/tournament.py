"""Le mode tournoi : rejouer un tournoi international du passé.

Le joueur du site affronte les équipes du tournoi de la moins bien classée
à la gagnante, en Bo5 (3 victoires). Pour chaque Bo5 :

1. il aligne 5 joueurs de sa collection face aux 5 joueurs adverses ;
2. à chaque partie, une draft de tournoi à tour de rôle (3 bans, 3 picks,
   2 bans, 2 picks de chaque côté), en Fearless : un champion joué dans le
   Bo5 ne revient plus, pour personne ;
3. la partie se joue au score : celui des équipes (note du joueur, note
   sur le champion, bonus de rareté, cohérence), corrigé à chaque poste par
   le face-à-face des deux champions (leur bilan en compétition). Le
   meilleur score gagne.

L'adversaire virtuel pioche ses champions parmi ceux que ses joueurs ont
vraiment joués pendant ce tournoi (le plus joué a le plus de chances), et
les notes de ses joueurs sont celles de ce tournoi. Ses cartes montent en
rareté au fil du parcours : communes au début, légendaires contre le
vainqueur. Gagner un Bo5 donne une carte d'un des joueurs affrontés :
commune jusqu'à la moitié du tournoi, rare ensuite.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, time as dtime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from esport.catalog import ewc_q, riot_events_q, side_events_q
from esport.models import Champion, GameAppearance, League, Split, Team
from esport.scoring import composition_coherence, composition_score, slot_score, unplayed_rating

from .models import (
    ROLES,
    Card,
    CardChampion,
    Rarity,
    Tournament,
    TournamentRun,
    TournamentSeries,
)
from .services import GameError, add_copies, best_rarities

#: Durée d'un tournoi créé automatiquement (jours) ; chaque tournoi reste
#: modifiable dans la gestion.
DEFAULT_DAYS = getattr(settings, "GAME_TOURNAMENT_DAYS", 7)

#: Le face-à-face : bilan lissé vers 50 % (comme MATCHUP_PRIOR parties
#: nulles en plus), puis converti en points de poste : 60 % de victoires
#: donnent +MATCHUP_WEIGHT/10 points, et l'adversaire autant en moins.
MATCHUP_PRIOR = 10
MATCHUP_WEIGHT = 40


# --------------------------------------------------------------- Les tournois
def playable_splits():
    """Les tournois internationaux importés et jouables (First Stand, MSI,
    Worlds, EWC), sans les qualifications."""
    leagues = League.objects.filter(riot_events_q() | ewc_q())
    return (
        Split.objects.filter(league__in=leagues, is_playable=True, cards__isnull=False)
        .exclude(side_events_q())
        .distinct()
    )


def standings(split: Split) -> list[int]:
    """Les équipes du tournoi, de la moins bien classée à la gagnante.

    Classement reconstitué depuis les parties : une équipe éliminée plus
    tard est mieux classée (à égalité, celle qui a gagné le plus de
    parties) ; le vainqueur de la dernière partie est premier. Seules
    les équipes avec une carte à chaque poste sont gardées."""
    rows = (
        GameAppearance.objects.filter(tournament__split=split, team__isnull=False)
        .values("team")
        .annotate(last=Max("played_at"), wins=Count("game_id", filter=Q(win=True), distinct=True))
    )
    complete = {
        team for team, n in Card.objects.filter(split=split, is_active=True, team__isnull=False)
        .values_list("team").annotate(n=Count("role", distinct=True)) if n >= len(ROLES)
    }
    far_past = timezone.make_aware(datetime(1970, 1, 1))
    order = sorted(
        (r for r in rows if r["team"] in complete),
        key=lambda r: (r["last"] or far_past, r["wins"], r["team"]),
    )
    teams = [r["team"] for r in order]
    final = (
        GameAppearance.objects.filter(tournament__split=split, win=True, team__in=teams)
        .order_by("-played_at").values_list("team", flat=True).first()
    )
    if final in teams:
        teams.remove(final)
        teams.append(final)
    return teams


def opponent_cards(split: Split, team_id: int) -> dict[str, int]:
    """Le cinq de départ d'une équipe sur ce tournoi : à chaque poste, le
    joueur qui y a joué le plus de parties."""
    lineup = {}
    cards = Card.objects.filter(split=split, team_id=team_id, is_active=True).select_related("stats")
    for card in sorted(cards, key=lambda c: (c.stats.games if c.stats else 0, c.overall), reverse=True):
        lineup.setdefault(card.role, card.pk)
    return lineup


def create_tournament(split: Split, starts_at=None, days: float | None = None, auto: bool = False) -> Tournament:
    starts_at = starts_at or timezone.now()
    teams = standings(split)
    if len(teams) < 2:
        raise GameError(f"{split} n'a pas assez d'équipes complètes pour un tournoi.")
    names = dict(Team.objects.filter(pk__in=teams).values_list("pk", "name"))
    return Tournament.objects.create(
        split=split, starts_at=starts_at, ends_at=starts_at + timedelta(days=days or DEFAULT_DAYS), auto=auto,
        opponents=[{"team": t, "name": names.get(t, "?")} for t in teams],
    )


def ensure_tournament(now=None) -> Tournament | None:
    """Le tournoi automatique en cours ; s'il n'y en a pas, en crée un (à
    partir de minuit, pour DEFAULT_DAYS jours) sur le tournoi international
    rejoué le moins récemment."""
    now = now or timezone.now()
    current = Tournament.objects.filter(auto=True, is_active=True, starts_at__lte=now, ends_at__gt=now).first()
    if current:
        return current
    lock = "tournament:creating"
    if not cache.add(lock, 1, 60):  # une autre requête est en train de le créer
        return None
    try:
        last = dict(
            Tournament.objects.filter(auto=True).values("split").annotate(last=Max("starts_at"))
            .values_list("split", "last")
        )
        candidates = list(playable_splits())
        rng = random.Random(timezone.localdate(now).toordinal())
        rng.shuffle(candidates)
        far_past = timezone.make_aware(datetime(1970, 1, 1))
        for split in sorted(candidates, key=lambda s: last.get(s.pk, far_past)):
            try:
                start = timezone.make_aware(datetime.combine(timezone.localdate(now), dtime.min))
                return create_tournament(split, starts_at=start, auto=True)
            except GameError:
                continue
        return None
    finally:
        cache.delete(lock)


def open_tournaments(now=None) -> list[Tournament]:
    now = now or timezone.now()
    return list(
        Tournament.objects.filter(is_active=True, starts_at__lte=now, ends_at__gt=now)
        .select_related("split__league").order_by("ends_at")
    )


def is_open(tournament: Tournament, now=None) -> bool:
    now = now or timezone.now()
    return tournament.is_active and tournament.starts_at <= now < tournament.ends_at


# --------------------------------------------------------------- Progression
def rarity_for_step(step: int, total: int) -> Rarity:
    """Les cartes adverses : communes au début, légendaires contre le
    dernier adversaire."""
    rarities = list(Rarity.objects.order_by("order"))
    if total <= 1:
        return rarities[-1]
    return rarities[round(step / (total - 1) * (len(rarities) - 1))]


def reward_rarity(step: int, total: int) -> Rarity:
    """Commune jusqu'à la moitié du tournoi, rare ensuite."""
    rarities = list(Rarity.objects.order_by("order"))
    return rarities[0] if step < total / 2 else rarities[min(1, len(rarities) - 1)]


def get_run(user, tournament: Tournament) -> TournamentRun:
    return TournamentRun.objects.get_or_create(tournament=tournament, user=user)[0]


def run_status(run: TournamentRun, now=None) -> dict:
    """{"open": Bo5 en cours ou None, "next_at": prochain Bo5 possible,
    "can_start", "finished"}."""
    now = now or timezone.now()
    current = run.series.exclude(status__in=[TournamentSeries.Status.WON, TournamentSeries.Status.LOST]).first()
    last = run.series.filter(finished_at__isnull=False).order_by("-finished_at").first()
    next_at = None
    # L'attente suit chaque Bo5, gagné ou perdu, sauf une défaite contre le
    # premier adversaire : on le retente tout de suite.
    first_try = last is not None and last.step == 0 and last.status == TournamentSeries.Status.LOST
    if last and not first_try:
        next_at = last.finished_at + timedelta(hours=float(run.tournament.cooldown_hours))
        if next_at <= now:
            next_at = None
    finished = run.finished
    return {
        "open": current,
        "next_at": next_at,
        "finished": finished,
        "can_start": current is None and next_at is None and not finished and is_open(run.tournament, now),
    }


def start_series(user, tournament: Tournament, now=None) -> TournamentSeries:
    if not is_open(tournament, now):
        raise GameError("Ce tournoi est fermé.")
    with transaction.atomic():
        run = TournamentRun.objects.select_for_update().get(pk=get_run(user, tournament).pk)
        status = run_status(run, now)
        if status["open"]:
            return status["open"]
        if status["finished"]:
            raise GameError("Tu as battu toutes les équipes de ce tournoi.")
        if status["next_at"]:
            raise GameError("Tu dois attendre avant ton prochain Bo5.")
        total = len(tournament.opponents)
        team_id = tournament.opponents[run.step]["team"]
        lineup = opponent_cards(tournament.split, team_id)
        if len(lineup) < len(ROLES):
            raise GameError("Cette équipe n'a plus de cinq complet : préviens l'admin.")
        series = TournamentSeries(
            run=run, step=run.step, team_id=team_id, opponent=lineup,
            opponent_rarity=rarity_for_step(run.step, total),
        )
        prepare(series)
        series.save()
        return series


def set_lineup(user, series: TournamentSeries, picks: dict) -> TournamentSeries:
    """picks : {poste: id de carte de la collection}."""
    if series.run.user_id != user.pk:
        raise GameError("Ce Bo5 n'est pas le tien.")
    if series.status != TournamentSeries.Status.LINEUP:
        raise GameError("Tes joueurs sont déjà choisis pour ce Bo5.")
    card_ids = {}
    for role in ROLES:
        try:
            card_ids[role] = int(picks.get(role) or 0)
        except (TypeError, ValueError):
            card_ids[role] = 0
    if not all(card_ids.values()):
        raise GameError("Choisis un joueur à chaque poste.")
    owned = best_rarities(user, list(card_ids.values()))
    cards = Card.objects.select_related("player").in_bulk(list(card_ids.values()))
    players = set()
    for role, card_id in card_ids.items():
        card = cards.get(card_id)
        if card is None or card_id not in owned:
            raise GameError("Tu ne peux aligner que des cartes de ta collection.")
        if card.role != role:
            raise GameError(f"{card.player.ign} joue {card.role}, pas {role}.")
        if card.player_id in players:
            raise GameError(f"{card.player.ign} ne peut pas jouer deux postes.")
        players.add(card.player_id)
    series.lineup = card_ids
    series.lineup_rarities = {role: owned[cid].pk for role, cid in card_ids.items()}
    series.status = TournamentSeries.Status.DRAFT
    series.draft = new_draft(series)
    prepare(series)
    _advance(series)  # côté rouge : l'adversaire commence
    series.save()
    return series


# --------------------------------------------------------------- Une partie
def matchup(role: str, champion: str, opposite: str) -> dict:
    """Le bilan de `champion` face à `opposite` au même poste, dans toutes
    les parties importées : {"games", "wins", "rate" (lissé, 0–1)}."""
    if champion == opposite:
        return {"games": 0, "wins": 0, "rate": 0.5}
    key = "matchup:" + hashlib.md5(f"{role}|{champion}|{opposite}".encode()).hexdigest()
    found = cache.get(key)
    if found is None:
        theirs = GameAppearance.objects.filter(role=role, champion=opposite).values("game_id")
        rows = GameAppearance.objects.filter(role=role, champion=champion, game_id__in=theirs)
        games = rows.order_by().values("game_id").distinct().count()
        wins = rows.filter(win=True).order_by().values("game_id").distinct().count()
        found = {"games": games, "wins": wins}
        cache.set(key, found, 24 * 3600)
    rate = (found["wins"] + MATCHUP_PRIOR / 2) / (found["games"] + MATCHUP_PRIOR)
    return {**found, "rate": rate}


def matchup_points(rate: float) -> float:
    return round((rate - 0.5) * MATCHUP_WEIGHT, 1)


#: L'ordre d'une draft de tournoi : (côté, action). 3 bans chacun, 3 picks
#: chacun, 2 bans chacun, 2 picks chacun.
DRAFT_ORDER = [
    ("blue", "ban"), ("red", "ban"), ("blue", "ban"), ("red", "ban"), ("blue", "ban"), ("red", "ban"),
    ("blue", "pick"), ("red", "pick"), ("red", "pick"), ("blue", "pick"), ("blue", "pick"), ("red", "pick"),
    ("red", "ban"), ("blue", "ban"), ("red", "ban"), ("blue", "ban"),
    ("red", "pick"), ("blue", "pick"), ("blue", "pick"), ("red", "pick"),
]


def new_draft(series: TournamentSeries) -> dict:
    """La draft d'une nouvelle partie. Partie 1 : le joueur du site est côté
    bleu ; ensuite, le perdant de la partie précédente prend le côté bleu."""
    side = "blue"
    if series.games:
        side = "red" if series.games[-1]["won"] else "blue"
    return {"side": side, "actions": [], "special": special_rule(series)}


# --------------------------------------------------------- La particularité
#: La particularité d'une partie : dans chaque camp, un joueur « en vedette », dont
#: les champions préférés suivent leur forme à l'époque du tournoi. Le bilan
#: de chaque champion cette année-là (toutes les parties importées), lissé
#: vers 50 % comme SPECIAL_PRIOR parties nulles, donne des points :
#: 60 % de victoires → +SPECIAL_WEIGHT/10 points sur son poste.
SPECIAL_FAVORITES = 3
SPECIAL_PRIOR = 20
SPECIAL_WEIGHT = 60
SPECIAL_CAP = 8.0


def era_rate(champion: str, year: int) -> dict:
    """Le bilan d'un champion sur une année de compétition."""
    key = "era:" + hashlib.md5(f"{champion}|{year}".encode()).hexdigest()
    found = cache.get(key)
    if found is None:
        rows = GameAppearance.objects.filter(champion=champion, tournament__split__year=year)
        found = {"games": rows.count(), "wins": rows.filter(win=True).count()}
        cache.set(key, found, 24 * 3600)
    rate = (found["wins"] + SPECIAL_PRIOR / 2) / (found["games"] + SPECIAL_PRIOR)
    return {**found, "rate": rate}


def _effects(champions: list[tuple[int, str]], year: int) -> list[dict]:
    effects = []
    for champion_id, name in champions[:SPECIAL_FAVORITES]:
        form = era_rate(name, year)
        points = round(max(-SPECIAL_CAP, min(SPECIAL_CAP, (form["rate"] - 0.5) * SPECIAL_WEIGHT)), 1)
        effects.append({"champion": champion_id, "name": name, "rate": round(form["rate"] * 100),
                        "games": form["games"], "points": points})
    return effects


def special_rule(series: TournamentSeries, rng=None) -> dict | None:
    """Tire la particularité d'une partie (None avant le choix des joueurs) :
    dans chaque équipe, un joueur en vedette dont les champions préférés
    suivent leur forme de l'année du tournoi. Elle compte pour les deux
    camps : {"year", "user": {...}, "opponent": {...}}, chaque côté avec
    {"role", "player", "effects": [{champion, name, rate, games, points}]}."""
    if not series.lineup:
        return None
    rng = rng or random
    year = series.run.tournament.split.year
    names = {cid: name for name, cid in champion_ids_by_name().items()}
    out = {"year": year}

    role = rng.choice(ROLES)
    card = Card.objects.select_related("player").get(pk=int(series.lineup[role]))
    favorites, seen = [], set()
    for a in CardChampion.objects.filter(card__player=card.player_id).order_by("-games", "-history_games"):
        if a.champion_id not in seen and a.champion_id in names:
            seen.add(a.champion_id)
            favorites.append((a.champion_id, names[a.champion_id]))
    if favorites:
        out["user"] = {"role": role, "player": card.player.ign, "effects": _effects(favorites, year)}

    role = rng.choice(ROLES)
    card = Card.objects.select_related("player").get(pk=int(series.opponent[role]))
    pool = sorted(prepare(series).get("pools", {}).get(role, []), key=lambda p: -p[1])
    favorites = [(cid, names[cid]) for cid, _ in pool if cid in names]
    if favorites:
        out["opponent"] = {"role": role, "player": card.player.ign, "effects": _effects(favorites, year)}
    return out if ("user" in out or "opponent" in out) else None


def special_points(special: dict | None, team: str, role: str, champion_id: int) -> float:
    """Les points de la particularité pour ce champion, à ce poste, dans ce camp."""
    side = (special or {}).get(team)
    if not side or side["role"] != role:
        return 0.0
    return next((e["points"] for e in side["effects"] if int(e["champion"]) == int(champion_id)), 0.0)


def _team_of(draft: dict, color: str) -> str:
    return "user" if color == draft["side"] else "opponent"


def next_turn(draft: dict) -> dict | None:
    """{"team": "user"|"opponent", "type": "ban"|"pick", "color", "index"} ou None."""
    index = len(draft["actions"])
    if index >= len(DRAFT_ORDER):
        return None
    color, kind = DRAFT_ORDER[index]
    return {"team": _team_of(draft, color), "type": kind, "color": color, "index": index}


def draft_state(series: TournamentSeries) -> dict:
    """Ce que la draft en cours a déjà fixé."""
    draft = series.draft or {"side": "red" if series.games and series.games[-1]["won"] else "blue", "actions": []}
    out = {"side": draft["side"], "special": draft.get("special"), "bans": {"user": [], "opponent": []},
           "picks": {"user": {}, "opponent": {}}, "turn": next_turn(draft)}
    for action in draft["actions"]:
        if action["type"] == "ban":
            out["bans"][action["team"]].append(action["champion"])
        else:
            out["picks"][action["team"]][action["role"]] = action["champion"]
    out["taken"] = (
        series.used_champions()
        | set(out["bans"]["user"]) | set(out["bans"]["opponent"])
        | set(out["picks"]["user"].values()) | set(out["picks"]["opponent"].values())
    )
    return out


def champion_ids_by_name() -> dict[str, int]:
    found = cache.get("champions:by-name")
    if found is None:
        found = dict(Champion.objects.values_list("name", "pk"))
        cache.set("champions:by-name", found, 3600)
    return found


def prepare(series: TournamentSeries) -> dict:
    """Ce que l'adversaire virtuel sait du Bo5, calculé une seule fois (puis
    gardé dans series.prepared) : pour chacun de ses joueurs, les champions
    joués pendant le tournoi et combien de fois (sinon son historique) ;
    et les meilleurs champions des joueurs du site, ses cibles de ban."""
    if series.prepared.get("pools") and (series.prepared.get("targets") or not series.lineup):
        return series.prepared
    by_name = champion_ids_by_name()
    prepared = dict(series.prepared)
    if not prepared.get("pools"):
        cards = Card.objects.in_bulk([int(c) for c in series.opponent.values()])
        pools = {}
        for role in ROLES:
            card = cards[int(series.opponent[role])]
            played = (
                GameAppearance.objects.filter(tournament__split=card.split_id, player=card.player_id)
                .values("champion").annotate(n=Count("id")).order_by("-n")
            )
            pool = [[by_name[r["champion"]], r["n"]] for r in played if r["champion"] in by_name]
            if not pool:
                pool = [
                    [cid, n] for cid, n in CardChampion.objects.filter(card__player=card.player_id)
                    .values("champion").annotate(n=Count("id")).values_list("champion", "n")
                ]
            pools[role] = pool
        prepared["pools"] = pools
    if series.lineup and not prepared.get("targets"):
        prepared["targets"] = {
            role: list(
                CardChampion.objects.filter(card_id=int(series.lineup[role]), games__gt=0)
                .order_by("-rating").values_list("champion_id", flat=True)[:12]
            )
            for role in ROLES
        }
    series.prepared = prepared
    return prepared


def opponent_action(series: TournamentSeries, state: dict, kind: str, rng=None) -> dict:
    """Le coup de l'adversaire virtuel (sans requête : tout est préparé).

    Pick : un de ses postes encore libres et un champion tiré parmi ce que
    ce joueur a joué pendant le tournoi (plus il l'a joué, plus il a de
    chances de le reprendre). Ban : un des meilleurs champions des joueurs
    du site encore sans champion."""
    rng = rng or random
    taken = state["taken"]
    prepared = prepare(series)
    if kind == "pick":
        options = [
            (role, cid, n)
            for role in ROLES if role not in state["picks"]["opponent"]
            for cid, n in prepared["pools"].get(role, []) if cid not in taken
        ]
        if options:
            role, champion, _ = rng.choices(options, weights=[n for *_, n in options])[0]
        else:
            role = next(r for r in ROLES if r not in state["picks"]["opponent"])
            champion = rng.choice([cid for cid in champion_ids_by_name().values() if cid not in taken])
        return {"team": "opponent", "type": "pick", "champion": champion, "role": role}

    open_roles = [r for r in ROLES if r not in state["picks"]["user"]] or ROLES
    targets = prepared.get("targets") or {}
    best = [cid for r in open_roles for cid in targets.get(r, [])[:4] if cid not in taken]
    best = best or [cid for cid in champion_ids_by_name().values() if cid not in taken]
    return {"team": "opponent", "type": "ban", "champion": rng.choice(best[:3] if len(best) > 3 else best),
            "role": None}


def _advance(series: TournamentSeries, rng=None, now=None) -> dict | None:
    """Joue les coups de l'adversaire jusqu'au tour du joueur du site, puis,
    si la draft est complète, joue la partie. Renvoie la partie jouée."""
    draft = series.draft or new_draft(series)
    series.draft = draft
    while True:
        turn = next_turn(draft)
        if turn is None:
            return _play(series, rng, now)
        if turn["team"] == "user":
            return None
        state = draft_state(series)
        draft["actions"].append(opponent_action(series, state, turn["type"], rng))


def _play(series: TournamentSeries, rng=None, now=None) -> dict:
    state = draft_state(series)
    special = (series.draft or {}).get("special")
    result = evaluate_game(series, state["picks"]["user"], state["picks"]["opponent"], special)
    game = {
        **result,
        "number": len(series.games) + 1,
        "side": state["side"],
        "special": special,
        "actions": list((series.draft or {}).get("actions", [])),
        "user_picks": {r: state["picks"]["user"][r] for r in ROLES},
        "opponent_picks": {r: state["picks"]["opponent"][r] for r in ROLES},
        "bans": state["bans"],
    }
    series.games = [*series.games, game]
    if result["won"]:
        series.wins += 1
    else:
        series.losses += 1
    series.draft = {}
    if series.wins >= TournamentSeries.WINS_NEEDED or series.losses >= TournamentSeries.WINS_NEEDED:
        _finish(series, rng, now)
    else:
        series.draft = new_draft(series)
        _advance(series, rng, now)  # l'adversaire ouvre peut-être la draft suivante
    return game


def auto_move(series: TournamentSeries, state: dict) -> dict:
    """Le coup du mode automatique, volontairement simple : pick, le
    champion libre le mieux noté pour un des joueurs du site encore sans
    champion (sans regarder le face-à-face, la cohérence ni la particularité) ;
    ban, le champion libre que l'adversaire a le plus joué pendant le tournoi."""
    taken = state["taken"]
    if state["turn"]["type"] == "pick":
        best = None
        open_roles = [r for r in ROLES if r not in state["picks"]["user"]]
        cards = Card.objects.select_related("stats").in_bulk([int(series.lineup[r]) for r in open_roles])
        for role in open_roles:
            card = cards[int(series.lineup[role])]
            top = (
                CardChampion.objects.filter(card=card).exclude(champion_id__in=taken)
                .order_by("-rating").values_list("champion_id", "rating").first()
            )
            if top and (best is None or top[1] > best[2]):
                best = (role, top[0], top[1])
        if best is None:
            role = open_roles[0]
            champion = next(cid for cid in sorted(champion_ids_by_name().values()) if cid not in taken)
            return {"champion": champion, "role": role}
        return {"champion": best[1], "role": best[0]}
    counts: dict[int, int] = {}
    for pool in prepare(series).get("pools", {}).values():
        for cid, n in pool:
            counts[cid] = counts.get(cid, 0) + n
    ranked = sorted((cid for cid in counts if cid not in taken), key=lambda c: -counts[c])
    champion = ranked[0] if ranked else next(
        cid for cid in sorted(champion_ids_by_name().values()) if cid not in taken
    )
    return {"champion": champion, "role": None}


def draft_action(user, series: TournamentSeries, champion_id, role: str | None = None, rng=None, now=None):
    """Le coup du joueur du site (ban, ou pick pour un de ses postes), puis
    ceux de l'adversaire jusqu'à son prochain tour. Renvoie la partie si la
    draft s'est terminée, sinon None."""
    if series.run.user_id != user.pk:
        raise GameError("Ce Bo5 n'est pas le tien.")
    if not is_open(series.run.tournament, now):
        raise GameError("Ce tournoi est fermé.")
    with transaction.atomic():
        series = TournamentSeries.objects.select_for_update().select_related("run__tournament", "opponent_rarity").get(
            pk=series.pk
        )
        if series.status != TournamentSeries.Status.DRAFT:
            raise GameError("Ce Bo5 n'attend pas de draft.")
        if not series.draft:
            series.draft = new_draft(series)
        state = draft_state(series)
        turn = state["turn"]
        if turn is None or turn["team"] != "user":
            raise GameError("Ce n'est pas ton tour.")
        if champion_id == "auto":
            move = auto_move(series, state)
            champion_id, role = move["champion"], move["role"]
        try:
            champion_id = int(champion_id or 0)
        except (TypeError, ValueError):
            champion_id = 0
        champion = Champion.objects.filter(pk=champion_id).first()
        if champion is None:
            raise GameError("Choisis un champion.")
        if champion_id in series.used_champions():
            raise GameError(f"Fearless : {champion.name} a déjà été joué dans ce Bo5.")
        if champion_id in state["taken"]:
            raise GameError(f"{champion.name} est déjà banni ou pris dans cette partie.")
        action = {"team": "user", "type": turn["type"], "champion": champion_id, "role": None}
        if turn["type"] == "pick":
            if role not in ROLES or role in state["picks"]["user"]:
                raise GameError("Choisis lequel de tes joueurs prend ce champion.")
            action["role"] = role
        series.draft["actions"].append(action)
        before = len(series.games)
        _advance(series, rng, now)
        series.save()
    return series.games[before] if len(series.games) > before else None


def _side(cards: dict[str, Card], champions: dict[str, Champion], rarities: dict[str, Rarity]) -> dict:
    ratings = {
        (a.card_id, a.champion_id): a
        for a in CardChampion.objects.filter(
            card_id__in=[c.pk for c in cards.values()], champion_id__in=[c.pk for c in champions.values()]
        )
    }
    slots = {}
    for role in ROLES:
        card, champion = cards[role], champions[role]
        player_rating = card.stats.score if card.stats else 50.0
        affinity = ratings.get((card.pk, champion.pk))
        champion_rating = affinity.rating if affinity else unplayed_rating(player_rating)
        slots[role] = {
            "card": card.pk,
            "player": card.player.ign,
            "player_rating": round(player_rating, 1),
            "champion": champion.pk,
            "champion_name": champion.name,
            "champion_rating": round(champion_rating, 1),
            "games": affinity.games + affinity.history_games if affinity else 0,
            "base": slot_score(player_rating, champion_rating, rarities[role].score_bonus),
        }
    return slots


def evaluate_game(series: TournamentSeries, user_picks: dict[str, int], opponent_picks: dict[str, int],
                  special: dict | None = None) -> dict:
    """Les deux scores d'une partie, poste par poste, avec les face-à-face
    et la particularité de la partie."""
    from .services import _duo_pairs

    all_ids = [int(c) for c in list(user_picks.values()) + list(opponent_picks.values())]
    champions = Champion.objects.in_bulk(all_ids)
    card_ids = [int(c) for c in list(series.lineup.values()) + list(series.opponent.values())]
    cards = Card.objects.select_related("player", "stats").in_bulk(card_ids)
    user_rarities = Rarity.objects.in_bulk([int(r) for r in series.lineup_rarities.values()])

    user = _side(
        {r: cards[int(series.lineup[r])] for r in ROLES},
        {r: champions[int(user_picks[r])] for r in ROLES},
        {r: user_rarities[int(series.lineup_rarities[r])] for r in ROLES},
    )
    them = _side(
        {r: cards[int(series.opponent[r])] for r in ROLES},
        {r: champions[int(opponent_picks[r])] for r in ROLES},
        {r: series.opponent_rarity for r in ROLES},
    )
    for role in ROLES:
        face = matchup(role, user[role]["champion_name"], them[role]["champion_name"])
        points = matchup_points(face["rate"])
        mine = special_points(special, "user", role, user[role]["champion"])
        theirs = special_points(special, "opponent", role, them[role]["champion"])
        user[role].update(matchup=round(face["rate"] * 100), matchup_games=face["games"], bonus=points,
                          special=mine, score=round(user[role]["base"] + points + mine, 1))
        them[role].update(matchup=100 - round(face["rate"] * 100), matchup_games=face["games"], bonus=-points,
                          special=theirs, score=round(them[role]["base"] - points + theirs, 1))

    def total(side):
        names = [side[r]["champion_name"] for r in ROLES]
        pairs = _duo_pairs(set(names))
        duos = sum(1 for i, a in enumerate(names) for b in names[i + 1:] if frozenset((a, b)) in pairs)
        coherence = composition_coherence([champions[side[r]["champion"]].profile for r in ROLES], duos_seen=duos)
        return coherence["score"], composition_score([side[r]["score"] for r in ROLES], coherence["score"])

    user_coherence, user_score = total(user)
    their_coherence, their_score = total(them)
    won = (user_score, user_coherence) > (their_score, their_coherence)  # égalité : l'adversaire
    return {
        "user": {r: user[r] for r in ROLES}, "opponent": {r: them[r] for r in ROLES},
        "user_score": user_score, "opponent_score": their_score,
        "user_coherence": user_coherence, "opponent_coherence": their_coherence,
        "won": won,
    }


def _finish(series: TournamentSeries, rng=None, now=None) -> None:
    rng = rng or random
    series.finished_at = now or timezone.now()
    run = series.run
    if series.wins >= TournamentSeries.WINS_NEEDED:
        series.status = TournamentSeries.Status.WON
        total = len(run.tournament.opponents)
        card_id = rng.choice(sorted(int(c) for c in series.opponent.values()))
        rarity = reward_rarity(series.step, total)
        add_copies(run.user, card_id, rarity.pk)
        series.reward_card_id, series.reward_rarity = card_id, rarity
        run.step = series.step + 1
        run.save(update_fields=["step"])
    else:
        series.status = TournamentSeries.Status.LOST


def series_view(series: TournamentSeries) -> dict:
    """De quoi afficher un Bo5 : les cartes, les champions interdits."""
    card_ids = [int(c) for c in list(series.lineup.values()) + list(series.opponent.values())]
    cards = Card.objects.select_related("player", "split", "team", "image", "stats").in_bulk(card_ids)
    used = series.used_champions()
    champions = Champion.objects.in_bulk(list(used))
    return {
        "opponent": [(r, cards.get(int(series.opponent[r]))) for r in ROLES],
        "lineup": [(r, cards.get(int(series.lineup[r]))) if series.lineup else (r, None) for r in ROLES],
        "banned": sorted((champions[c] for c in used if c in champions), key=lambda c: c.name),
    }

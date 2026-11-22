"""Le 1V1 : un Bo5 entre deux joueurs du site, avec les règles du mode
tournoi (cards/tournament.py) : 5 joueurs de sa collection, draft de
tournoi à tour de rôle en Fearless, partie décidée au score (notes, rareté,
face-à-face, cohérence, particularité de la partie).

En plus, une méta tirée pour chaque duel, comme celle de l'équipe de la
semaine (cards/weekly.py) : un bonus par poste, qui s'ajoute au bonus de
rareté.

Le temps : 2 min pour choisir ses joueurs (sinon les mieux notés de sa
collection), 30 s par coup de draft (sinon le coup automatique). Après 3
coups manqués, le joueur passe en automatique jusqu'à la fin du duel. Les
délais sont appliqués à chaque consultation du duel (tick), sans tâche de fond.
"""
from __future__ import annotations

import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from esport.models import Champion
from esport.scoring import composition_coherence, composition_score, slot_score, unplayed_rating

from . import weekly
from .models import ROLES, Card, CardChampion, Duel, Rarity
from .services import GameError, best_rarities, copies_by_card
from .tournament import DRAFT_ORDER, _effects, champion_ids_by_name, matchup, matchup_points

INVITE_HOURS = 24
LINEUP_SECONDS = 120
TURN_SECONDS = 30
AUTO_AFTER_MISSES = 3
MAX_OPEN_INVITES = 5


def other(side: str) -> str:
    return "b" if side == "a" else "a"


def _locked(duel: Duel) -> Duel:
    return Duel.objects.select_for_update().select_related("challenger", "opponent").get(pk=duel.pk)


def _side(duel: Duel, user) -> str:
    side = duel.side_of(user)
    if side is None:
        raise GameError("Ce duel n'est pas le tien.")
    return side


# --------------------------------------------------------------------- Invitations
def open_duel_between(a, b) -> Duel | None:
    return Duel.objects.filter(status__in=Duel.OPEN).filter(
        Q(challenger=a, opponent=b) | Q(challenger=b, opponent=a)).first()


def invite(user, target) -> Duel:
    if target is None or not target.is_active:
        raise GameError("Ce joueur n'existe pas.")
    if target.pk == user.pk:
        raise GameError("Tu ne peux pas te défier toi-même.")
    if open_duel_between(user, target):
        raise GameError(f"Un duel est déjà en cours avec {target.username}.")
    if Duel.objects.filter(challenger=user, status=Duel.Status.INVITED).count() >= MAX_OPEN_INVITES:
        raise GameError(f"Tu as déjà {MAX_OPEN_INVITES} invitations en attente.")
    if missing_roles(user):
        raise GameError("Il te faut un joueur à chaque poste dans ta collection pour défier quelqu'un.")
    return Duel.objects.create(challenger=user, opponent=target)


def respond(user, duel: Duel, accept: bool, now=None) -> Duel:
    now = now or timezone.now()
    with transaction.atomic():
        duel = _locked(duel)
        tick(duel, now)
        if duel.opponent_id != user.pk or duel.status != Duel.Status.INVITED:
            raise GameError("Cette invitation n'est plus valable.")
        if not accept:
            duel.status = Duel.Status.DECLINED
            duel.finished_at = now
        else:
            if missing_roles(user):
                raise GameError("Il te faut un joueur à chaque poste dans ta collection pour accepter.")
            duel.status = Duel.Status.LINEUP
            duel.meta = weekly.generate_rules_seeded(f"duel-{duel.pk}-{now.timestamp()}")
            duel.deadline = now + timedelta(seconds=LINEUP_SECONDS)
        duel.save()
    return duel


def cancel(user, duel: Duel, now=None) -> Duel:
    now = now or timezone.now()
    with transaction.atomic():
        duel = _locked(duel)
        if duel.challenger_id != user.pk or duel.status != Duel.Status.INVITED:
            raise GameError("Cette invitation ne peut plus être annulée.")
        duel.status = Duel.Status.CANCELLED
        duel.finished_at = now
        duel.save()
    return duel


def forfeit(user, duel: Duel, now=None) -> Duel:
    now = now or timezone.now()
    with transaction.atomic():
        duel = _locked(duel)
        side = _side(duel, user)
        if duel.status not in (Duel.Status.LINEUP, Duel.Status.DRAFT):
            raise GameError("Ce duel n'est pas en cours.")
        _finish(duel, other(side), now, forfeit=True)
        duel.save()
    return duel


# --------------------------------------------------------------------- Joueurs alignés
def missing_roles(user) -> list[str]:
    roles = set(Card.objects.filter(owners__user=user, owners__quantity__gt=0).values_list("role", flat=True))
    return [r for r in ROLES if r not in roles]


def _validate_lineup(user, picks: dict) -> tuple[dict, dict]:
    card_ids = {}
    for role in ROLES:
        try:
            card_ids[role] = int((picks or {}).get(role) or 0)
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
    return card_ids, {role: owned[cid].pk for role, cid in card_ids.items()}


def auto_lineup(user) -> dict | None:
    """Les joueurs les mieux notés de sa collection, un par poste (None s'il en manque)."""
    copies = copies_by_card(user)
    cards = sorted(Card.objects.filter(pk__in=list(copies)), key=lambda c: -c.overall)
    picks, players = {}, set()
    for card in cards:
        if card.role in ROLES and card.role not in picks and card.player_id not in players:
            picks[card.role] = card.pk
            players.add(card.player_id)
    return picks if len(picks) == len(ROLES) else None


def set_lineup(user, duel: Duel, picks: dict, now=None) -> Duel:
    now = now or timezone.now()
    with transaction.atomic():
        duel = _locked(duel)
        tick(duel, now)
        side = _side(duel, user)
        if duel.status != Duel.Status.LINEUP:
            raise GameError("Ce n'est plus le moment de choisir tes joueurs.")
        if getattr(duel, f"lineup_{side}"):
            raise GameError("Tes joueurs sont déjà choisis.")
        card_ids, rarities = _validate_lineup(user, picks)
        setattr(duel, f"lineup_{side}", card_ids)
        setattr(duel, f"rarities_{side}", rarities)
        if duel.lineup_a and duel.lineup_b:
            _start_draft(duel, now)
        duel.save()
    return duel


# --------------------------------------------------------------------- Draft
def new_draft(duel: Duel) -> dict:
    """Partie 1 : l'invitant est côté bleu ; ensuite le perdant de la partie
    précédente prend le côté bleu."""
    blue = "a"
    if duel.games:
        blue = other(duel.games[-1]["winner"])
    return {"blue": blue, "actions": [], "special": special_rule(duel)}


def special_rule(duel: Duel, rng=None) -> dict | None:
    """La particularité de la partie, comme au tournoi : dans chaque camp, un
    joueur en vedette dont les champions préférés gagnent ou perdent des
    points selon leur bilan l'année de sa carte."""
    rng = rng or random
    names = {cid: name for name, cid in champion_ids_by_name().items()}
    out = {}
    for side in ("a", "b"):
        lineup = getattr(duel, f"lineup_{side}")
        if not lineup:
            continue
        role = rng.choice(ROLES)
        card = Card.objects.select_related("player", "split").get(pk=int(lineup[role]))
        favorites, seen = [], set()
        for a in CardChampion.objects.filter(card__player=card.player_id).order_by("-games", "-history_games"):
            if a.champion_id not in seen and a.champion_id in names:
                seen.add(a.champion_id)
                favorites.append((a.champion_id, names[a.champion_id]))
        if favorites:
            year = card.split.year
            out[side] = {"role": role, "player": card.player.ign, "year": year, "effects": _effects(favorites, year)}
    return out or None


def special_points(special: dict | None, side: str, role: str, champion_id: int) -> float:
    entry = (special or {}).get(side)
    if not entry or entry["role"] != role:
        return 0.0
    return next((e["points"] for e in entry["effects"] if int(e["champion"]) == int(champion_id)), 0.0)


def next_turn(draft: dict) -> dict | None:
    index = len(draft.get("actions", []))
    if not draft or index >= len(DRAFT_ORDER):
        return None
    color, kind = DRAFT_ORDER[index]
    team = draft["blue"] if color == "blue" else other(draft["blue"])
    return {"team": team, "type": kind, "color": color, "index": index}


def draft_state(duel: Duel) -> dict:
    draft = duel.draft or {}
    out = {"bans": {"a": [], "b": []}, "picks": {"a": {}, "b": {}}, "turn": next_turn(draft) if draft else None}
    for action in draft.get("actions", []):
        if action["type"] == "ban":
            out["bans"][action["team"]].append(action["champion"])
        else:
            out["picks"][action["team"]][action["role"]] = action["champion"]
    out["taken"] = (duel.used_champions() | set(out["bans"]["a"]) | set(out["bans"]["b"])
                    | set(out["picks"]["a"].values()) | set(out["picks"]["b"].values()))
    return out


def _start_draft(duel: Duel, now) -> None:
    duel.status = Duel.Status.DRAFT
    duel.draft = new_draft(duel)
    duel.deadline = now + timedelta(seconds=TURN_SECONDS)
    _auto_play(duel, now)


def auto_move(duel: Duel, side: str, state: dict) -> dict:
    """Le coup automatique (comme le mode auto du tournoi) : pick, le champion
    libre le mieux noté d'un de ses joueurs encore sans champion ; ban, le
    champion libre le plus joué par les joueurs d'en face."""
    taken = state["taken"]
    lineup = getattr(duel, f"lineup_{side}")
    if state["turn"]["type"] == "pick":
        open_roles = [r for r in ROLES if r not in state["picks"][side]]
        best = None
        for role in open_roles:
            top = (CardChampion.objects.filter(card_id=int(lineup[role])).exclude(champion_id__in=taken)
                   .order_by("-rating").values_list("champion_id", "rating").first())
            if top and (best is None or top[1] > best[2]):
                best = (role, top[0], top[1])
        if best is None:
            role = open_roles[0]
            champion = next(cid for cid in sorted(champion_ids_by_name().values()) if cid not in taken)
            return {"team": side, "type": "pick", "champion": champion, "role": role}
        return {"team": side, "type": "pick", "champion": best[1], "role": best[0]}
    theirs = [int(c) for c in getattr(duel, f"lineup_{other(side)}").values()]
    ranked = (CardChampion.objects.filter(card_id__in=theirs).exclude(champion_id__in=taken)
              .values("champion_id").annotate(n=Sum("games")).order_by("-n").values_list("champion_id", flat=True))
    champion = next(iter(ranked), None) or next(
        cid for cid in sorted(champion_ids_by_name().values()) if cid not in taken)
    return {"team": side, "type": "ban", "champion": champion, "role": None}


def _apply(duel: Duel, action: dict, now) -> None:
    duel.draft["actions"].append(action)
    duel.draft = dict(duel.draft)
    duel.deadline = now + timedelta(seconds=TURN_SECONDS)
    if next_turn(duel.draft) is None:
        _play_game(duel, now)


def _auto_play(duel: Duel, now) -> None:
    """Joue tout de suite les coups des joueurs passés en automatique."""
    while duel.status == Duel.Status.DRAFT:
        state = draft_state(duel)
        turn = state["turn"]
        if turn is None or not getattr(duel, f"auto_{turn['team']}"):
            return
        _apply(duel, auto_move(duel, turn["team"], state), now)


def play(user, duel: Duel, kind: str, champion_id=None, role: str | None = None, now=None) -> Duel:
    """Le coup d'un joueur : ban ou pick (champion "auto" : le coup automatique)."""
    now = now or timezone.now()
    with transaction.atomic():
        duel = _locked(duel)
        tick(duel, now)
        side = _side(duel, user)
        if duel.status != Duel.Status.DRAFT:
            raise GameError("Ce duel n'attend pas de draft.")
        state = draft_state(duel)
        turn = state["turn"]
        if turn is None or turn["team"] != side:
            raise GameError("Ce n'est pas ton tour.")
        if turn["type"] != kind:
            raise GameError("Ce n'est pas le moment.")
        if champion_id == "auto":
            action = auto_move(duel, side, state)
        else:
            try:
                champion_id = int(champion_id or 0)
            except (TypeError, ValueError):
                champion_id = 0
            champion = Champion.objects.filter(pk=champion_id).first()
            if champion is None:
                raise GameError("Choisis un champion.")
            if champion_id in duel.used_champions():
                raise GameError(f"Fearless : {champion.name} a déjà été joué dans ce Bo5.")
            if champion_id in state["taken"]:
                raise GameError(f"{champion.name} est déjà banni ou pris dans cette partie.")
            action = {"team": side, "type": kind, "champion": champion_id, "role": None}
            if kind == "pick":
                if role not in ROLES or role in state["picks"][side]:
                    raise GameError("Choisis lequel de tes joueurs prend ce champion.")
                action["role"] = role
        _apply(duel, action, now)
        _auto_play(duel, now)
        duel.save()
    return duel


# --------------------------------------------------------------------- Délais
def tick(duel: Duel, now=None) -> bool:
    """Applique les délais dépassés. Renvoie True si le duel a changé (à
    enregistrer par l'appelant, sous verrou)."""
    now = now or timezone.now()
    changed = False
    if duel.status == Duel.Status.INVITED and duel.created_at and duel.created_at < now - timedelta(hours=INVITE_HOURS):
        duel.status = Duel.Status.EXPIRED
        duel.finished_at = now
        return True
    if duel.status == Duel.Status.LINEUP and duel.deadline and duel.deadline <= now:
        missing = [s for s in ("a", "b") if not getattr(duel, f"lineup_{s}")]
        failed = []
        for side in missing:
            picks = auto_lineup(duel.user_of(side))
            if picks is None:
                failed.append(side)
                continue
            card_ids, rarities = _validate_lineup(duel.user_of(side), picks)
            setattr(duel, f"lineup_{side}", card_ids)
            setattr(duel, f"rarities_{side}", rarities)
        if len(failed) == 2:
            duel.status = Duel.Status.CANCELLED
            duel.finished_at = now
        elif failed:
            _finish(duel, other(failed[0]), now, forfeit=True)
        else:
            _start_draft(duel, now)
        return True
    guard = 0
    while duel.status == Duel.Status.DRAFT and duel.deadline and duel.deadline <= now and guard < 100:
        guard += 1
        state = draft_state(duel)
        turn = state["turn"]
        if turn is None:
            break
        side = turn["team"]
        missed = getattr(duel, f"missed_{side}") + 1
        setattr(duel, f"missed_{side}", missed)
        if missed >= AUTO_AFTER_MISSES:
            setattr(duel, f"auto_{side}", True)
        base = duel.deadline
        _apply(duel, auto_move(duel, side, state), base)
        _auto_play(duel, base)
        changed = True
    return changed


def refresh(duel: Duel, now=None) -> Duel:
    """Le duel à jour (délais appliqués et enregistrés)."""
    now = now or timezone.now()
    if duel.status not in Duel.OPEN:
        return duel
    if duel.status != Duel.Status.INVITED and not (duel.deadline and duel.deadline <= now):
        return duel
    with transaction.atomic():
        duel = _locked(duel)
        if tick(duel, now):
            duel.save()
    return duel


# --------------------------------------------------------------------- Une partie
def _side_slots(duel: Duel, side: str, picks: dict, champions: dict, cards: dict, rarities: dict) -> dict:
    lineup = getattr(duel, f"lineup_{side}")
    rules = duel.meta or {}
    games = weekly.champion_games() if any(not r.get("on_card") for r in rules.values()) else None
    ratings = {(a.card_id, a.champion_id): a for a in CardChampion.objects.filter(
        card_id__in=[int(c) for c in lineup.values()], champion_id__in=[int(c) for c in picks.values()])}
    slots = {}
    for role in ROLES:
        card = cards[int(lineup[role])]
        champion = champions[int(picks[role])]
        rarity = rarities[int(getattr(duel, f"rarities_{side}")[role])]
        player_rating = card.stats.score if card.stats else 50.0
        affinity = ratings.get((card.pk, champion.pk))
        champion_rating = affinity.rating if affinity else unplayed_rating(player_rating)
        rule = rules.get(role)
        meta = weekly.card_bonus(rule, card) + weekly.champion_bonus(rule, champion, games)
        slots[role] = {
            "card": card.pk, "player": card.player.ign, "player_rating": round(player_rating, 1),
            "champion": champion.pk, "champion_name": champion.name, "champion_rating": round(champion_rating, 1),
            "games": affinity.games + affinity.history_games if affinity else 0,
            "meta": meta,
            "base": slot_score(player_rating, champion_rating, float(rarity.score_bonus) + meta),
        }
    return slots


def evaluate(duel: Duel, picks_a: dict, picks_b: dict, special: dict | None = None) -> dict:
    from .services import _duo_pairs

    champions = Champion.objects.in_bulk([int(c) for c in list(picks_a.values()) + list(picks_b.values())])
    cards = Card.objects.select_related("player", "stats", "split__league", "team").in_bulk(
        [int(c) for c in list(duel.lineup_a.values()) + list(duel.lineup_b.values())])
    rarities = Rarity.objects.in_bulk([int(r) for r in list(duel.rarities_a.values()) + list(duel.rarities_b.values())])
    a = _side_slots(duel, "a", picks_a, champions, cards, rarities)
    b = _side_slots(duel, "b", picks_b, champions, cards, rarities)
    for role in ROLES:
        face = matchup(role, a[role]["champion_name"], b[role]["champion_name"])
        points = matchup_points(face["rate"])
        mine = special_points(special, "a", role, a[role]["champion"])
        theirs = special_points(special, "b", role, b[role]["champion"])
        a[role].update(matchup=round(face["rate"] * 100), matchup_games=face["games"], bonus=points,
                       special=mine, score=round(a[role]["base"] + points + mine, 1))
        b[role].update(matchup=100 - round(face["rate"] * 100), matchup_games=face["games"], bonus=-points,
                       special=theirs, score=round(b[role]["base"] - points + theirs, 1))

    def total(slots):
        names = [slots[r]["champion_name"] for r in ROLES]
        pairs = _duo_pairs(set(names))
        duos = sum(1 for i, x in enumerate(names) for y in names[i + 1:] if frozenset((x, y)) in pairs)
        coherence = composition_coherence([champions[slots[r]["champion"]].profile for r in ROLES], duos_seen=duos)
        return coherence["score"], composition_score([slots[r]["score"] for r in ROLES], coherence["score"])

    coh_a, score_a = total(a)
    coh_b, score_b = total(b)
    rating_a = sum(a[r]["player_rating"] for r in ROLES)
    rating_b = sum(b[r]["player_rating"] for r in ROLES)
    # Égalité : la cohérence, puis les notes des joueurs, puis le côté rouge.
    key_a, key_b = (score_a, coh_a, rating_a), (score_b, coh_b, rating_b)
    if key_a == key_b:
        winner = other((duel.draft or {}).get("blue", "a"))
    else:
        winner = "a" if key_a > key_b else "b"
    return {"a": a, "b": b, "score_a": score_a, "score_b": score_b, "coherence_a": coh_a, "coherence_b": coh_b,
            "winner": winner}


def _play_game(duel: Duel, now) -> None:
    state = draft_state(duel)
    draft = duel.draft
    result = evaluate(duel, state["picks"]["a"], state["picks"]["b"], draft.get("special"))
    game = {
        **result, "number": len(duel.games) + 1, "blue": draft["blue"], "special": draft.get("special"),
        "actions": list(draft.get("actions", [])), "bans": state["bans"],
        "picks_a": {r: state["picks"]["a"][r] for r in ROLES}, "picks_b": {r: state["picks"]["b"][r] for r in ROLES},
    }
    duel.games = [*duel.games, game]
    setattr(duel, f"wins_{result['winner']}", getattr(duel, f"wins_{result['winner']}") + 1)
    if max(duel.wins_a, duel.wins_b) >= Duel.WINS_NEEDED:
        _finish(duel, "a" if duel.wins_a > duel.wins_b else "b", now)
    else:
        duel.draft = new_draft(duel)
        duel.deadline = now + timedelta(seconds=TURN_SECONDS)


def _finish(duel: Duel, winner_side: str, now, forfeit: bool = False) -> None:
    duel.status = Duel.Status.FINISHED
    duel.winner = duel.user_of(winner_side)
    duel.forfeit = forfeit
    duel.finished_at = now
    duel.deadline = None
    duel.draft = {}


# --------------------------------------------------------------------- Listes
def duels_of(user):
    return Duel.objects.filter(Q(challenger=user) | Q(opponent=user)).select_related("challenger", "opponent", "winner")


def waiting_count(user) -> int:
    """Invitations reçues + duels où c'est à toi de jouer (pastille de l'onglet)."""
    n = Duel.objects.filter(opponent=user, status=Duel.Status.INVITED).count()
    for duel in duels_of(user).filter(status__in=[Duel.Status.LINEUP, Duel.Status.DRAFT]):
        side = duel.side_of(user)
        if duel.status == Duel.Status.LINEUP and not getattr(duel, f"lineup_{side}"):
            n += 1
        elif duel.status == Duel.Status.DRAFT:
            turn = next_turn(duel.draft or {})
            if turn and turn["team"] == side:
                n += 1
    return n


def opponents_for(user):
    return get_user_model().objects.filter(is_active=True).exclude(pk=user.pk).order_by("username")

"""Services du jeu : cartes, collection (raretés, fusions, échanges),
boosters (réserve et ouverture), équipes et classement."""
from __future__ import annotations

import itertools
import logging
import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.text import slugify

from esport.champions import champion_key
from esport.models import Champion, GameAppearance, League, PlayerImage, PlayerSplitStats, Split
from esport.scoring import (
    champion_rating,
    composition_coherence,
    composition_score,
    overall_rating,
    slot_score,
    team_score,
    unplayed_rating,
)

from . import catalog, draw
from .models import (
    COMPOSITIONS,
    ROLES,
    Booster,
    BoosterGift,
    BoosterStock,
    Card,
    CardChampion,
    PackOpening,
    PackOpeningCard,
    Rarity,
    UserCard,
)

logger = logging.getLogger(__name__)

# Raretés par défaut :
# (code, nom, ordre, couleur, taux de tirage %, bonus de score %, exemplaires pour fusionner)
DEFAULT_RARITIES = [
    ("commune", "Commune", 0, "#9aa4b1", 74, 0, 2),
    ("rare", "Rare", 1, "#3b82f6", 20, 5, 2),
    ("epique", "Épique", 2, "#a855f7", 5, 10, 2),
    ("legendaire", "Légendaire", 3, "#f5b301", 1, 20, 0),
]


class GameError(Exception):
    pass


def ensure_default_rarities() -> int:
    created = 0
    for code, name, order, color, rate, bonus, fusion in DEFAULT_RARITIES:
        _, was_created = Rarity.objects.get_or_create(
            code=code,
            defaults={"name": name, "order": order, "color": color, "drop_rate": rate,
                      "score_bonus": bonus, "fusion_cost": fusion},
        )
        created += was_created
    if created:
        # Un doublon de légendaire s'échange contre le joueur de son choix, en rare.
        Rarity.objects.filter(code="legendaire").update(
            exchange_cost=1, exchange_rarity=Rarity.objects.filter(code="rare").first()
        )
    return created


#: Les régions Leaguepedia, en français pour le nom des boosters.
REGION_LABELS = {
    "Korea": "Corée",
    "China": "Chine",
    "Europe": "Europe",
    "EMEA": "EMEA",
    "North America": "Amérique du Nord",
    "Americas": "Amériques",
    "Brazil": "Brésil",
    "Latin America": "Amérique latine",
    "Vietnam": "Viêt Nam",
    "Japan": "Japon",
    "PCS": "Asie-Pacifique",
    "Asia Pacific": "Asie-Pacifique",
    "Oceania": "Océanie",
    "Turkey": "Turquie",
    "CIS": "CEI",
    "International": "International",
}


def region_of(split: Split) -> str:
    return split.league.region or split.league.short_name or split.league.name


#: La couleur des boosters automatiques, par région (le reste garde la couleur par défaut).
REGION_COLORS = {
    "Korea": "#c8344b",
    "China": "#d9822b",
    "EMEA": "#2f6fd6",
    "Europe": "#2f6fd6",
    "North America": "#1f9e8a",
    "Americas": "#1f9e8a",
    "Brazil": "#2e9b4f",
    "Vietnam": "#b8443a",
    "Asia Pacific": "#7a4fd1",
    "PCS": "#7a4fd1",
    "Japan": "#c43c7a",
    "International": "#b8862e",
}
DEFAULT_BOOSTER_COLOR = "#1f6feb"

#: Les boosters internationaux tombent moins souvent.
INTERNATIONAL_COOLDOWN_HOURS = 6
INTERNATIONAL_MAX_STOCK = 6

#: Les compétitions internationales de Riot (noms courts et morceaux de noms Leaguepedia).
RIOT_EVENTS_SHORT = {"MSI", "WCS", "FST", "WORLDS"}
RIOT_EVENTS_NAMES = ("mid-season invitational", "world championship", "first stand")
EWC_SHORT = {"EWC"}
EWC_NAMES = ("esports world cup",)


def booster_family(league) -> tuple[str, str, str]:
    """La famille de booster d'une ligue : (clé, libellé, couleur).

    Ligue locale : sa région (« Corée »). International : l'Esports World
    Cup à part, les compétitions de Riot (First Stand, MSI, Worlds)
    ensemble, le reste dans « International »."""
    short = (league.short_name or "").upper()
    name = (league.name or "").lower()
    if short in EWC_SHORT or any(n in name for n in EWC_NAMES):
        return "ewc", "Esports World Cup", "#0f8f6f"
    if short in RIOT_EVENTS_SHORT or any(n in name for n in RIOT_EVENTS_NAMES):
        return "riot-international", "Riot International", "#d13639"
    region = league.region or league.short_name or league.name
    label = REGION_LABELS.get(region, region)
    return region, label, REGION_COLORS.get(region, DEFAULT_BOOSTER_COLOR)


BASE_BOOSTER_SLUG = "base"
EWC_BOOSTER_SLUG = "ewc"


def ensure_boosters(deactivate_obsolete: bool = False) -> list[str]:
    """Les boosters types, tous avec la dernière carte au moins rare :

    - le booster de base (actif) : les joueurs LEC, LCK, LPL et LCS de
      toutes les années, seulement ceux des équipes qualifiées à un First
      Stand, un MSI ou des Worlds la même année ;
    - l'Esports World Cup, toutes années confondues (inactif) ;
    - un booster par région et par année, et Riot International par année
      (inactifs : à activer dans la gestion si on en veut).

    Ne touche pas aux réglages des boosters existants (actif ou non, rythme,
    visuel, taux) ; ajoute seulement les nouvelles ligues. deactivate_obsolete
    désactive les boosters automatiques qu'aucune famille ne produit plus
    (anciens découpages). Renvoie les noms des boosters créés."""
    from esport.catalog import ewc_q, major_leagues_q

    ensure_default_rarities()
    created = []
    rare = Rarity.objects.filter(code="rare").first()
    playable = Split.objects.filter(is_playable=True)
    wanted = {BASE_BOOSTER_SLUG, EWC_BOOSTER_SLUG}

    def ensure(slug, leagues, defaults):
        if not leagues:
            return
        booster, was_created = Booster.objects.get_or_create(
            slug=slug, defaults={"guaranteed_rarity": rare, "auto": True, **defaults}
        )
        booster.leagues.add(*leagues)
        if was_created:
            created.append(booster.name)
        return booster

    majors = list(League.objects.filter(major_leagues_q(), splits__in=playable).distinct())
    ensure(BASE_BOOSTER_SLUG, majors, {
        "name": "Booster de base",
        "description": (
            "Les joueurs LEC, LCK, LPL et LCS de toutes les années, des équipes qualifiées "
            "à un First Stand, un MSI ou des Worlds. Dernière carte au moins rare."
        ),
        "qualified_teams_only": True,
        "order": 0,
        "color": "#3fb6c9",
    })
    ewc = list(League.objects.filter(ewc_q(), splits__in=playable).distinct())
    ensure(EWC_BOOSTER_SLUG, ewc, {
        "name": "Esports World Cup",
        "description": "Les joueurs de l'Esports World Cup, toutes années confondues. Dernière carte au moins rare.",
        "is_active": False,
        "order": 1,
        "color": "#0f8f6f",
        "cooldown_hours": INTERNATIONAL_COOLDOWN_HOURS,
        "max_stock": INTERNATIONAL_MAX_STOCK,
        "starting_stock": 1,
    })

    groups: dict[tuple[str, int], dict] = {}
    for split in playable.select_related("league"):
        key, label, color = booster_family(split.league)
        if key == "ewc":
            continue
        group = groups.setdefault((key, split.year), {"label": label, "color": color, "leagues": set()})
        group["leagues"].add(split.league)

    for (key, year), group in sorted(groups.items(), key=lambda g: (-g[0][1], g[0][0])):
        slug = slugify(f"{key}-{year}")[:100]
        wanted.add(slug)
        international = key in {"riot-international", "International"}
        defaults = {
            "name": f"{group['label']} {year}",
            "description": (
                f"Les joueurs de {', '.join(sorted(l.short_name or l.name for l in group['leagues']))} en {year}. "
                "Dernière carte au moins rare."
            ),
            "years": str(year),
            "is_active": False,
            "order": 100 - (year % 100) + (5 if international else 0),
            "color": group["color"],
        }
        if international:
            defaults.update(cooldown_hours=INTERNATIONAL_COOLDOWN_HOURS, max_stock=INTERNATIONAL_MAX_STOCK,
                            starting_stock=1)
        ensure(slug, list(group["leagues"]), defaults)

    if deactivate_obsolete:
        Booster.objects.filter(auto=True, is_active=True).exclude(slug__in=wanted).update(is_active=False)
    catalog.bump()
    return created


#: Joueurs montrés en vitrine sur un booster sans visuel.
SHOWCASE_SIZE = 6


def format_rate(percent: float) -> str:
    """Un petit pourcentage lisible : 2 chiffres significatifs, virgule."""
    if percent <= 0:
        return "0 %"
    text = f"{percent:.2g}" if percent < 1 else f"{percent:.1f}".rstrip("0").rstrip(".")
    if "e" in text:
        text = f"{percent:.6f}".rstrip("0")
    return f"{text.replace('.', ',')} %"


def booster_showcase(booster: Booster, size: int = SHOWCASE_SIZE, rng=None) -> list[dict]:
    """Quelques joueurs tirés au hasard dans le contenu du booster, à montrer
    en légendaire, avec la chance de tirer ce joueur en légendaire sur une
    carte : [{"card", "rarity", "rate"}]."""
    rng = rng or random
    ids = catalog.pool_ids(booster)
    top = Rarity.objects.order_by("-order").first()
    if not ids or top is None:
        return []
    rules = catalog.rules(booster)
    total = sum(r.weight for r in rules) or 1
    top_share = next((r.weight for r in rules if r.key == top.pk), 0) / total
    rate = format_rate(100 * top_share / len(ids))
    chosen = rng.sample(ids, min(size, len(ids)))
    cards = Card.objects.filter(pk__in=chosen).select_related("player", "team", "image", "split")
    order = {pk: i for i, pk in enumerate(chosen)}
    return [{"card": c, "rarity": top, "rate": rate} for c in sorted(cards, key=lambda c: order[c.pk])]


def rarity_rules(booster: Booster | None = None) -> list[draw.RarityRule]:
    overrides = {}
    if booster is not None and booster.pk:
        overrides = {w.rarity_id: w.rate for w in booster.rarity_weights.all()}
    return [
        draw.RarityRule(key=r.pk, order=r.order, weight=float(overrides.get(r.pk, r.drop_rate)))
        for r in Rarity.objects.all()
    ]


# --------------------------------------------------------------------- Cartes
def _pick_image(card: Card, split: Split, images: list[PlayerImage]):
    candidates = [
        {"key": img.pk, "tournament": img.tournament_name, "team": img.team_name} for img in images
    ]
    key = draw.best_image(
        candidates,
        split_name=split.name,
        league_short=split.league.short_name,
        year=split.year,
        card_team=card.team.name if card.team else "",
    )
    return key


@transaction.atomic
def generate_cards(split: Split) -> tuple[int, int]:
    """Crée/met à jour une carte par joueur ayant joué la période.
    Respecte la photo verrouillée dans la gestion."""
    if not Rarity.objects.exists():
        ensure_default_rarities()

    stats_qs = PlayerSplitStats.objects.filter(split=split).select_related("player", "team")
    images_by_player: dict[int, list[PlayerImage]] = {}
    for img in PlayerImage.objects.filter(player__in=stats_qs.values("player")):
        images_by_player.setdefault(img.player_id, []).append(img)

    created = updated = 0
    for stats in stats_qs:
        card = Card.objects.filter(player=stats.player, split=split).first()
        if card is None:
            card = Card(player=stats.player, split=split)
            created += 1
        else:
            updated += 1
        card.stats = stats
        card.team = stats.team
        card.role = stats.role
        card.overall = overall_rating(stats.score)
        if not card.image_locked:
            card.image_id = _pick_image(card, split, images_by_player.get(stats.player_id, []))
        card.save()

    # Les cartes dont le joueur n'a plus de stats (données corrigées) sont désactivées.
    Card.objects.filter(split=split, stats__isnull=True).update(is_active=False)
    compute_card_champions(split)
    refresh_rosters_of_split(split)
    catalog.bump()
    return created, updated


def compute_card_champions(split: Split) -> int:
    """Les champions de chaque carte, avec la note du joueur sur chacun :
    ses parties de la période (la forme du moment) et, comptées à moitié,
    ses parties d'avant sur ce champion (son historique)."""
    cards = list(Card.objects.filter(split=split))
    player_ids = {c.player_id for c in cards}
    end = split.end_date
    current: dict[int, list] = {}
    history: dict[int, list] = {}
    for player_id, split_id, played_at, champion, rating, win, kills, deaths, assists in (
        GameAppearance.objects.filter(player_id__in=player_ids)
        .exclude(champion="")
        .values_list("player_id", "tournament__split_id", "played_at", "champion", "rating", "win",
                     "kills", "deaths", "assists")
    ):
        row = (champion, rating, win, kills, deaths, assists)
        if split_id == split.pk:
            current.setdefault(player_id, []).append(row)
        elif end is None or played_at is None or played_at.date() <= end:
            # Seulement ce qui précède la fin de la période : une carte de
            # 2025 ne profite pas des parties de 2026.
            history.setdefault(player_id, []).append(row)

    names = {g[0] for rows in current.values() for g in rows} | {g[0] for rows in history.values() for g in rows}
    champions = {c.name: c for c in Champion.objects.filter(name__in=names)}
    for name in names - champions.keys():
        champions[name] = Champion.objects.create(name=name, key=champion_key(name))

    rows = []
    for card in cards:
        now_games = current.get(card.player_id, [])
        before = history.get(card.player_id, [])
        rated = [g[1] for g in now_games if g[1] is not None]
        player_mean = sum(rated) / len(rated) if rated else 50.0
        by_now: dict[str, list] = {}
        by_before: dict[str, list] = {}
        for g in now_games:
            by_now.setdefault(g[0], []).append(g)
        for g in before:
            by_before.setdefault(g[0], []).append(g)
        for name in by_now.keys() | by_before.keys():
            games_now, games_before = by_now.get(name, []), by_before.get(name, [])
            kills = sum(g[3] for g in games_now)
            deaths = sum(g[4] for g in games_now)
            assists = sum(g[5] for g in games_now)
            rows.append(
                CardChampion(
                    card=card,
                    champion=champions[name],
                    games=len(games_now),
                    wins=sum(1 for g in games_now if g[2]),
                    history_games=len(games_before),
                    history_wins=sum(1 for g in games_before if g[2]),
                    kda=round((kills + assists) / max(deaths, 1), 2) if games_now else 0,
                    rating=champion_rating(
                        [g[1] for g in games_now if g[1] is not None],
                        player_mean,
                        settings.GAME_RATING_PRIOR_GAMES,
                        history_scores=[g[1] for g in games_before if g[1] is not None],
                    ),
                )
            )
    with transaction.atomic():
        CardChampion.objects.filter(card__split=split).delete()
        CardChampion.objects.bulk_create(rows, batch_size=500)
    return len(rows)


def download_card_images(split: Split, importer) -> int:
    """Télécharge les photos utilisées par les cartes du split (+ photo principale en secours)."""
    cards = Card.objects.filter(split=split).select_related("image", "player")
    items = []
    for card in cards:
        if card.image:
            items.append((card.image, "filename", "file"))
        items.append((card.player, "image_filename", "photo"))
    # Dédoublonnage (un joueur peut apparaître plusieurs fois).
    unique = {(type(obj), obj.pk, f): (obj, a, f) for obj, a, f in items}
    return importer.download_images(list(unique.values()))


# --------------------------------------------------------------------- Collection
def best_rarities(user, card_ids=None) -> dict[int, Rarity]:
    """La meilleure rareté possédée de chaque carte."""
    rarities = {r.pk: r for r in Rarity.objects.all()}
    qs = UserCard.objects.filter(user=user, quantity__gt=0)
    if card_ids is not None:
        qs = qs.filter(card_id__in=list(card_ids))
    best: dict[int, Rarity] = {}
    for card_id, rarity_id in qs.values_list("card_id", "rarity_id"):
        rarity = rarities[rarity_id]
        if card_id not in best or rarity.order > best[card_id].order:
            best[card_id] = rarity
    return best


def copies_by_card(user, card_ids=None) -> dict[int, list[tuple[Rarity, int]]]:
    """{carte: [(rareté, exemplaires)]}, de la plus commune à la plus rare."""
    rarities = {r.pk: r for r in Rarity.objects.all()}
    qs = UserCard.objects.filter(user=user, quantity__gt=0)
    if card_ids is not None:
        qs = qs.filter(card_id__in=list(card_ids))
    out: dict[int, list] = {}
    for card_id, rarity_id, quantity in qs.values_list("card_id", "rarity_id", "quantity"):
        out.setdefault(card_id, []).append((rarities[rarity_id], quantity))
    for items in out.values():
        items.sort(key=lambda item: item[0].order)
    return out


def add_copies(user, card_id: int, rarity_id: int, quantity: int = 1) -> bool:
    """Ajoute des exemplaires ; renvoie True si le joueur n'avait pas encore
    cette carte (à aucune rareté)."""
    was_new = not UserCard.objects.filter(user=user, card_id=card_id, quantity__gt=0).exists()
    item, created = UserCard.objects.get_or_create(
        user=user, card_id=card_id, rarity_id=rarity_id, defaults={"quantity": quantity}
    )
    if not created:
        item.quantity += quantity
        item.save(update_fields=["quantity", "last_obtained_at"])
    return was_new


def _take_copies(user, card_id: int, rarity: Rarity, count: int, keep: int = 0) -> None:
    item = UserCard.objects.select_for_update().filter(user=user, card_id=card_id, rarity=rarity).first()
    have = item.quantity if item else 0
    if have < count + keep:
        raise GameError(f"Il te faut {count + keep} exemplaires {rarity.name.lower()}s (tu en as {have}).")
    item.quantity -= count
    if item.quantity:
        item.save(update_fields=["quantity"])
    else:
        item.delete()


def fuse(user, card_id: int, rarity_id: int) -> Rarity:
    """Fusionne des exemplaires d'une carte en un exemplaire de la rareté
    suivante (« 2 communes donnent une rare »)."""
    rarity = Rarity.objects.get(pk=rarity_id)
    target = rarity.next()
    if not rarity.fusion_cost or target is None:
        raise GameError(f"Les exemplaires {rarity.name.lower()}s ne se fusionnent pas.")
    with transaction.atomic():
        _take_copies(user, card_id, rarity, rarity.fusion_cost)
        add_copies(user, card_id, target.pk)
    refresh_rosters_of_user(user)
    return target


def exchange(user, card_id: int, rarity_id: int, rng=None):
    """Échange un doublon (« un doublon de légendaire ») : il est donné, et
    le joueur reçoit une proposition de ExchangeOffer.CHOICES cartes tirées
    au hasard parmi toutes les cartes jouables (des joueurs différents), à
    choisir ensuite (choose_exchange) à la rareté d'échange. L'exemplaire
    d'origine est gardé. Renvoie la proposition."""
    from .models import ExchangeOffer

    rng = rng or random
    rarity = Rarity.objects.select_related("exchange_rarity").get(pk=rarity_id)
    if not rarity.exchange_cost or rarity.exchange_rarity is None:
        raise GameError(f"Les doublons {rarity.name.lower()}s ne s'échangent pas.")
    pool = list(_exchangeable_cards().values_list("pk", "player_id"))
    by_player: dict[int, list[int]] = {}
    for pk, player_id in pool:
        by_player.setdefault(player_id, []).append(pk)
    if not by_player:
        raise GameError("Aucune carte à proposer pour l'instant.")
    players = rng.sample(sorted(by_player), min(ExchangeOffer.CHOICES, len(by_player)))
    picked = [rng.choice(by_player[p]) for p in players]
    with transaction.atomic():
        _take_copies(user, card_id, rarity, rarity.exchange_cost, keep=1)
        offer = ExchangeOffer.objects.create(
            user=user, given_card_id=card_id, given_rarity=rarity, rarity=rarity.exchange_rarity
        )
        offer.choices.set(picked)
    return offer


def _exchangeable_cards():
    return Card.objects.filter(is_active=True, split__is_playable=True)


def pending_exchanges(user):
    from .models import ExchangeOffer

    return ExchangeOffer.objects.filter(user=user, chosen__isnull=True).select_related("rarity", "given_card__player")


def choose_exchange(user, offer_id: int, card_id: int):
    """Le joueur prend une des cartes proposées. Renvoie (carte, rareté)."""
    from .models import ExchangeOffer

    with transaction.atomic():
        offer = ExchangeOffer.objects.select_for_update().filter(pk=offer_id, user=user).first()
        if offer is None:
            raise GameError("Cet échange n'existe pas.")
        if offer.chosen_id:
            raise GameError("Tu as déjà choisi ta carte pour cet échange.")
        card = offer.choices.filter(pk=card_id).first()
        if card is None:
            raise GameError("Choisis une des cartes proposées.")
        add_copies(user, card.pk, offer.rarity_id)
        offer.chosen = card
        offer.chosen_at = timezone.now()
        offer.save(update_fields=["chosen", "chosen_at"])
    refresh_rosters_of_user(user)
    return card, offer.rarity


# --------------------------------------------------------------------- Boosters
def _period(booster: Booster) -> timedelta:
    return timedelta(hours=float(booster.cooldown_hours))


def _regular_packs(stock: BoosterStock, now) -> tuple[int, object]:
    """La réserve qui se recharge : (disponibles, date du prochain)."""
    booster = stock.booster
    period = _period(booster)
    gained = max(0, int((now - stock.refilled_at) / period))
    available = min(booster.max_stock, stock.stock + gained)
    if available >= booster.max_stock:
        return available, None
    return available, stock.refilled_at + (gained + 1) * period


def available_packs(stock: BoosterStock, now=None) -> tuple[int, object]:
    """(boosters disponibles, date du prochain) — le prochain vaut None
    quand la réserve est pleine. Les boosters offerts s'ajoutent, sans
    plafond."""
    available, next_at = _regular_packs(stock, now or timezone.now())
    return available + stock.bonus, next_at


def get_stock(user, booster: Booster) -> BoosterStock:
    stock, _ = BoosterStock.objects.get_or_create(
        user=user,
        booster=booster,
        defaults={"stock": min(booster.starting_stock, booster.max_stock), "refilled_at": timezone.now()},
    )
    stock.booster = booster
    return stock


def booster_status(user, boosters) -> dict[int, dict]:
    """Pour chaque booster : {"available", "max", "next_at", "unlimited"}."""
    unlimited = bool(settings.GAME_STAFF_UNLIMITED and user.is_staff)
    stocks = {s.booster_id: s for s in BoosterStock.objects.filter(user=user, booster__in=boosters)}
    out = {}
    for booster in boosters:
        stock = stocks.get(booster.pk) or get_stock(user, booster)
        stock.booster = booster
        available, next_at = available_packs(stock)
        out[booster.pk] = {"available": available, "max": booster.max_stock, "next_at": next_at,
                           "unlimited": unlimited, "gifted": stock.bonus}
    return out


def _take_pack(user, booster: Booster) -> None:
    if settings.GAME_STAFF_UNLIMITED and user.is_staff:
        return
    get_stock(user, booster)
    stock = BoosterStock.objects.select_for_update().get(user=user, booster=booster)
    stock.booster = booster
    now = timezone.now()
    available, next_at = _regular_packs(stock, now)
    if available <= 0:
        if stock.bonus:
            # La réserve est vide : on prend un booster offert.
            stock.bonus -= 1
            stock.save(update_fields=["bonus"])
            return
        minutes = max(1, round((next_at - now).total_seconds() / 60))
        wait = f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d}"
        raise GameError(f"Plus de booster {booster.name} en réserve : le prochain arrive dans {wait}.")
    # La réserve d'abord : elle seule se recharge, et une réserve pleine
    # gaspillerait sa recharge.
    period = _period(booster)
    gained = max(0, int((now - stock.refilled_at) / period))
    if stock.stock + gained >= booster.max_stock:
        # Réserve pleine : ce qui dépassait est perdu, le compteur repart d'ici.
        stock.refilled_at = now
    else:
        stock.refilled_at += gained * period
    stock.stock = available - 1
    stock.save(update_fields=["stock", "refilled_at"])


MAX_GIFT = 100


def give_boosters(users, booster: Booster, quantity: int, given_by=None, message: str = "") -> int:
    """Offre des boosters à des joueurs du site : ils s'ajoutent à leur
    réserve, sans plafond. Renvoie le nombre de joueurs servis."""
    if quantity < 1 or quantity > MAX_GIFT:
        raise GameError(f"Entre 1 et {MAX_GIFT} boosters.")
    count = 0
    with transaction.atomic():
        for user in users:
            stock = get_stock(user, booster)
            BoosterStock.objects.filter(pk=stock.pk).update(bonus=F("bonus") + quantity)
            BoosterGift.objects.create(
                user=user, booster=booster, quantity=quantity, given_by=given_by, message=message[:200]
            )
            count += 1
    return count


def reset_progress(users, keep_gifts: bool = False) -> dict[str, int]:
    """Remet des joueurs du site à zéro : collection, ouvertures, équipes et
    réserves de boosters (ils retrouvent le stock de départ à leur prochaine
    visite). keep_gifts garde les boosters offerts encore à ouvrir.
    Renvoie le nombre de lignes supprimées par catégorie."""
    from .models import ExchangeOffer, PackOpening, Roster

    users = list(users)
    counts = {
        "players": len(users),
        "copies": UserCard.objects.filter(user__in=users).aggregate(n=Sum("quantity"))["n"] or 0,
    }
    with transaction.atomic():
        UserCard.objects.filter(user__in=users).delete()
        counts["openings"] = PackOpening.objects.filter(user__in=users).delete()[1].get("cards.PackOpening", 0)
        counts["rosters"] = Roster.objects.filter(user__in=users).delete()[1].get("cards.Roster", 0)
        ExchangeOffer.objects.filter(user__in=users).delete()
        stocks = BoosterStock.objects.filter(user__in=users)
        if keep_gifts:
            stocks.filter(bonus=0).delete()
            for stock in stocks.select_related("booster"):
                stock.stock = min(stock.booster.starting_stock, stock.booster.max_stock)
                stock.refilled_at = timezone.now()
                stock.save(update_fields=["stock", "refilled_at"])
        else:
            stocks.delete()
            BoosterGift.objects.filter(user__in=users).delete()
    return counts


def unseen_gifts(user) -> list:
    """Les cadeaux pas encore annoncés au joueur ; les marque comme vus."""
    gifts = list(BoosterGift.objects.filter(user=user, seen_at__isnull=True).select_related("booster", "given_by"))
    if gifts:
        BoosterGift.objects.filter(pk__in=[g.pk for g in gifts]).update(seen_at=timezone.now())
    return gifts


def open_booster(user, booster: Booster, rng=None) -> PackOpening:
    if not booster.is_active:
        raise GameError("Ce booster n'est pas disponible.")

    with transaction.atomic():
        # Verrouille l'utilisateur : deux ouvertures simultanées ne prennent pas le même booster.
        get_user_model().objects.select_for_update().filter(pk=user.pk).first()
        cards = catalog.pool_ids(booster)
        if not cards:
            raise GameError("Ce booster ne contient encore aucune carte.")
        _take_pack(user, booster)
        opening = _deal(user, booster, cards, rng)
    refresh_rosters_of_user(user)
    return opening


def _deal(user, booster: Booster, cards: list[int], rng=None, portal=None) -> PackOpening:
    """Tire les cartes d'un booster et les donne au joueur (dans une transaction)."""
    guaranteed = booster.guaranteed_rarity.order if booster.guaranteed_rarity else None
    picks = draw.draw_pack(cards, catalog.rules(booster), booster.cards_per_pack,
                           guaranteed_order=guaranteed, rng=rng)
    opening = PackOpening.objects.create(user=user, booster=booster, portal=portal)
    for slot, (rarity_id, card_id) in enumerate(picks, start=1):
        was_new = add_copies(user, card_id, rarity_id)
        PackOpeningCard.objects.create(
            opening=opening, card_id=card_id, rarity_id=rarity_id, slot=slot, was_new=was_new
        )
    return opening


# --------------------------------------------------------------------- Portails
DAILY_PORTAL_HOURS = 24
DAILY_PORTAL_OPENINGS = 2
DAILY_PORTAL_WAIT_HOURS = 12


def ensure_daily_portal(now=None):
    """Le portail du jour : de minuit à minuit (heure du site), sur un
    booster inactif qui n'a pas eu de portail depuis le plus longtemps (au
    hasard parmi les ex æquo). Créé à la première visite du jour. Aucun
    booster inactif avec des cartes : pas de portail."""
    from datetime import datetime, time as dtime

    from .models import Portal

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    start = timezone.make_aware(datetime.combine(today, dtime.min))
    existing = Portal.objects.filter(auto=True, starts_at=start).first()
    if existing:
        return existing
    candidates = [b for b in Booster.objects.filter(is_active=False) if catalog.has_cards(b)]
    if not candidates:
        return None
    last = {}
    for booster_id, starts_at in Portal.objects.filter(auto=True).values_list("booster_id", "starts_at"):
        last[booster_id] = max(starts_at, last.get(booster_id, starts_at))
    oldest = min(last.get(b.pk, start.replace(year=1970)) for b in candidates)
    pool = [b for b in candidates if last.get(b.pk, start.replace(year=1970)) == oldest]
    booster = random.Random(today.toordinal()).choice(sorted(pool, key=lambda b: b.pk))
    try:
        with transaction.atomic():
            return Portal.objects.create(
                booster=booster, auto=True, starts_at=start, ends_at=start + timedelta(hours=DAILY_PORTAL_HOURS),
                max_openings=DAILY_PORTAL_OPENINGS, wait_hours=DAILY_PORTAL_WAIT_HOURS,
            )
    except IntegrityError:  # créé au même instant par une autre requête
        return Portal.objects.filter(auto=True, starts_at=start).first()


def open_portals(now=None):
    from .models import Portal

    now = now or timezone.now()
    return list(
        Portal.objects.filter(is_active=True, starts_at__lte=now, ends_at__gt=now)
        .select_related("booster", "booster__guaranteed_rarity")
        .order_by("ends_at")
    )


def portal_status(user, portal, now=None) -> dict:
    """{"used", "max", "available", "next_at", "done"} pour un joueur.
    done : plus rien à ouvrir (tout ouvert, ou le prochain tomberait après
    la fermeture)."""
    now = now or timezone.now()
    unlimited = bool(settings.GAME_STAFF_UNLIMITED and user.is_staff)
    opened = list(
        PackOpening.objects.filter(user=user, portal=portal).order_by("opened_at").values_list("opened_at", flat=True)
    )
    used = len(opened)
    status = {"used": used, "max": portal.max_openings, "available": False, "next_at": None,
              "done": False, "too_late": False, "unlimited": unlimited}
    if unlimited:
        status["available"] = True
        return status
    if used >= portal.max_openings:
        status["done"] = True
        return status
    next_at = opened[-1] + timedelta(hours=float(portal.wait_hours)) if opened else portal.starts_at
    if next_at >= portal.ends_at:
        status["done"] = status["too_late"] = True
    elif next_at <= now:
        status["available"] = True
    else:
        status["next_at"] = next_at
    return status


def open_portal(user, portal, rng=None, now=None) -> PackOpening:
    now = now or timezone.now()
    if not portal.is_active or not (portal.starts_at <= now < portal.ends_at):
        raise GameError("Ce portail est fermé.")
    with transaction.atomic():
        get_user_model().objects.select_for_update().filter(pk=user.pk).first()
        status = portal_status(user, portal, now)
        if not status["available"]:
            if status["done"]:
                raise GameError("Tu as ouvert tout ce que ce portail propose.")
            raise GameError("Le prochain booster de ce portail n'est pas encore prêt.")
        cards = catalog.pool_ids(portal.booster)
        if not cards:
            raise GameError("Ce booster ne contient encore aucune carte.")
        opening = _deal(user, portal.booster, cards, rng, portal=portal)
    refresh_rosters_of_user(user)
    return opening


# --------------------------------------------------------------------- Équipes
def _parse_picks(picks: dict) -> dict[str, tuple[int, list]]:
    """{rôle: id} ou {rôle: {"card": id, "champions": [ids]}} (ou
    "champion": id) → {rôle: (id de carte, [5 ids de champion ou None])}."""
    out = {}
    for role, pick in (picks or {}).items():
        if isinstance(pick, dict):
            card_id = pick.get("card")
            champions = pick.get("champions")
            if champions is None and pick.get("champion"):
                champions = [pick["champion"]]
        else:
            card_id, champions = pick, []
        if not card_id:
            continue
        champions = [int(c) if c else None for c in (champions or [])][:COMPOSITIONS]
        champions += [None] * (COMPOSITIONS - len(champions))
        out[role] = (int(card_id), champions)
    return out


def _duo_pairs(champion_names: set[str]) -> set[frozenset]:
    """Les paires de ces champions déjà jouées ensemble en compétition."""
    teams: dict[tuple, set] = {}
    for game_id, team_id, name in GameAppearance.objects.filter(champion__in=champion_names).values_list(
        "game_id", "team_id", "champion"
    ):
        teams.setdefault((game_id, team_id), set()).add(name)
    pairs = set()
    for names in teams.values():
        if len(names) > 1:
            pairs.update(frozenset(p) for p in itertools.combinations(sorted(names), 2))
    return pairs


def evaluate_roster(user, picks: dict, check: bool = False) -> dict:
    """Le score d'une équipe, poste par poste et composition par composition.

    Score d'un poste = (moitié note du joueur sur la période, moitié note
    sur le champion) × bonus de rareté de son meilleur exemplaire.
    Composition n = le n-ième champion de chaque joueur ; son score mêle la
    moyenne de ses 5 postes et sa cohérence. Score de l'équipe = moyenne
    des 5 compositions, quand tout est rempli.

    check=True lève GameError sur un choix interdit (sinon, les erreurs
    sont listées dans "errors")."""
    choices = _parse_picks(picks)
    errors = []

    def fail(message):
        if check:
            raise GameError(message)
        errors.append(message)

    card_ids = [c for c, _ in choices.values()]
    cards = Card.objects.select_related("player", "split", "stats").in_bulk(card_ids)
    rarities = best_rarities(user, card_ids)
    champion_ids = {ch for _, chs in choices.values() for ch in chs if ch}
    champions = Champion.objects.in_bulk(champion_ids)
    ratings = {
        (a.card_id, a.champion_id): a
        for a in CardChampion.objects.filter(card_id__in=card_ids, champion_id__in=champion_ids)
    }

    slots, players = {}, set()
    for role in ROLES:
        if role not in choices:
            continue
        card_id, chosen = choices[role]
        card = cards.get(card_id)
        if card is None or card_id not in rarities:
            fail("Tu ne peux mettre que des cartes de ta collection.")
            continue
        if card.role != role:
            fail(f"{card.player.ign} joue {card.role}, pas {role}.")
            continue
        if card.player_id in players:
            fail(f"{card.player.ign} ne peut pas occuper deux postes.")
            continue
        players.add(card.player_id)
        seen = set()
        for champion_id in chosen:
            if not champion_id:
                continue
            if champion_id not in champions:
                fail("Champion inconnu.")
            elif champion_id in seen:
                fail(f"{card.player.ign} a deux fois {champions[champion_id].name} : 5 champions différents.")
            seen.add(champion_id)
        rarity = rarities[card_id]
        player_rating = card.stats.score if card.stats else 50.0
        entries = []
        for champion_id in chosen:
            champion = champions.get(champion_id) if champion_id else None
            if champion is None:
                entries.append(None)
                continue
            affinity = ratings.get((card_id, champion_id))
            rating = affinity.rating if affinity else unplayed_rating(player_rating)
            entries.append({
                "id": champion.pk,
                "name": champion.name,
                "rating": rating,
                "played": affinity is not None,
                "games": affinity.games if affinity else 0,
                "history_games": affinity.history_games if affinity else 0,
                "score": slot_score(player_rating, rating, rarity.score_bonus),
                "profile": champion.profile,
            })
        slots[role] = {
            "card": card_id,
            "player": card.player.ign,
            "player_rating": round(player_rating, 1),
            "rarity": {"id": rarity.pk, "name": rarity.name, "color": rarity.color,
                       "bonus": float(rarity.score_bonus)},
            "champions": entries,
        }

    names = {e["name"] for s in slots.values() for e in s["champions"] if e}
    pairs = _duo_pairs(names) if names else set()
    compositions = []
    for index in range(COMPOSITIONS):
        entries = [slots[role]["champions"][index] if role in slots else None for role in ROLES]
        filled = [e for e in entries if e]
        ids = [e["id"] for e in filled]
        if len(ids) != len(set(ids)):
            fail(f"Composition {index + 1} : un champion ne sert qu'une fois par composition.")
        comp = {"index": index + 1, "champions": [e["name"] if e else None for e in entries],
                "complete": len(filled) == len(ROLES)}
        if comp["complete"]:
            duos = sum(1 for p in itertools.combinations(sorted(e["name"] for e in filled), 2) if frozenset(p) in pairs)
            coherence = composition_coherence([e["profile"] for e in filled], duos_seen=duos)
            average = team_score([e["score"] for e in filled])
            comp.update(average=average, coherence=coherence, score=composition_score(
                [e["score"] for e in filled], coherence["score"]))
        compositions.append(comp)

    complete = len(slots) == len(ROLES) and all(c["complete"] for c in compositions) and not errors
    for s in slots.values():
        for e in s["champions"]:
            if e:
                e.pop("profile", None)
    return {
        "slots": slots,
        "compositions": compositions,
        "score": team_score([c["score"] for c in compositions]) if complete else None,
        "complete": complete,
        "errors": errors,
    }


def save_roster(user, name: str, picks: dict, roster=None):
    """Crée ou modifie une équipe (voir evaluate_roster pour picks)."""
    from .models import Roster, RosterPick, RosterSlot

    name = (name or "").strip()
    if not name:
        raise GameError("Donne un nom à ton équipe.")
    evaluate_roster(user, picks, check=True)
    choices = _parse_picks(picks)

    with transaction.atomic():
        if roster is None:
            roster = Roster.objects.create(user=user, name=name[:60])
        else:
            roster.name = name[:60]
            roster.save()
        roster.slots.exclude(role__in=choices.keys()).delete()
        for role, (card_id, champions) in choices.items():
            slot, _ = RosterSlot.objects.update_or_create(roster=roster, role=role, defaults={"card_id": card_id})
            slot.picks.all().delete()
            RosterPick.objects.bulk_create([
                RosterPick(slot=slot, composition=i, champion_id=champion_id)
                for i, champion_id in enumerate(champions, start=1)
                if champion_id
            ])
        refresh_roster(roster)
    return roster


def roster_picks(roster) -> dict:
    return {
        slot.role: {"card": slot.card_id, "champions": [c.pk if c else None for c in slot.champion_list()]}
        for slot in roster.slots.prefetch_related("picks__champion")
    }


def refresh_roster(roster) -> dict:
    """Recalcule et enregistre le score de l'équipe, de ses postes et de ses choix."""
    from .models import RosterPick

    result = evaluate_roster(roster.user, roster_picks(roster))
    for slot in roster.slots.prefetch_related("picks"):
        data = result["slots"].get(slot.role)
        scores = []
        for pick in slot.picks.all():
            entry = data["champions"][pick.composition - 1] if data else None
            pick.score = entry["score"] if entry else None
            if pick.score is not None:
                scores.append(pick.score)
        RosterPick.objects.bulk_update(slot.picks.all(), ["score"])
        slot.score = team_score(scores) if scores else None
        slot.save(update_fields=["score"])
    roster.score = result["score"]
    roster.details = {"compositions": result["compositions"], "complete": result["complete"]}
    roster.save(update_fields=["score", "details"])
    return result


def refresh_rosters_of_split(split: Split) -> int:
    from .models import Roster

    rosters = list(Roster.objects.filter(slots__card__split=split).distinct().select_related("user"))
    for roster in rosters:
        refresh_roster(roster)
    return len(rosters)


def refresh_rosters_of_user(user) -> int:
    from .models import Roster

    rosters = list(Roster.objects.filter(user=user))
    for roster in rosters:
        refresh_roster(roster)
    return len(rosters)


def leaderboard(limit: int = 100):
    """Le classement : la meilleure équipe complète de chaque joueur du site."""
    from .models import Roster

    best = {}
    rosters = (
        Roster.objects.filter(score__isnull=False)
        .select_related("user")
        .prefetch_related("slots__card__player", "slots__picks__champion")
        .order_by("-score", "updated_at")
    )
    for roster in rosters:
        best.setdefault(roster.user_id, roster)
    ranked = list(best.values())[:limit]
    return [(rank, roster) for rank, roster in enumerate(ranked, start=1)]


# --------------------------------------------------------------------- Retrait d'une ligue
@transaction.atomic
def remove_league_data(league) -> dict:
    """Retire tout ce que l'import d'une ligue a créé, en gardant la ligue
    elle-même (on peut la réimporter plus tard).

    Partent : splits, tournois, parties, stats, cartes et donc les
    exemplaires possédés par les joueurs du site et les postes d'équipe
    qui les utilisaient. Les joueurs pros qui n'ont plus aucune partie ni
    carte partent aussi, avec leurs photos.

    Boosters : la ligue quitte leurs filtres. Un booster automatique qui
    n'a plus de ligue est supprimé ; un booster fait à la main qui perd
    tous ses filtres de ligue ou de split est désactivé (sinon il
    contiendrait toutes les cartes)."""
    from esport.models import Player

    from .models import Roster

    splits = Split.objects.filter(league=league)
    cards = Card.objects.filter(split__in=splits)
    players = set(GameAppearance.objects.filter(tournament__league=league).values_list("player_id", flat=True))
    players |= set(cards.values_list("player_id", flat=True))
    rosters = list(Roster.objects.filter(slots__card__in=cards).distinct())
    boosters = list(Booster.objects.filter(leagues=league)) + list(
        Booster.objects.filter(splits__in=splits).exclude(leagues=league)
    )
    booster_filters = {
        b.pk: (b.leagues.exists(), b.splits.exists()) for b in boosters
    }

    summary = {
        "splits": splits.count(),
        "cards": cards.count(),
        "owned": UserCard.objects.filter(card__in=cards).count(),
        "rosters": len(rosters),
    }

    league.boosters.remove(*boosters)
    splits.delete()  # cascade : cartes, stats, affinités, exemplaires, postes
    league.tournaments.all().delete()  # cascade : parties

    deleted_boosters, disabled_boosters = [], []
    for booster in {b.pk: b for b in boosters}.values():
        had_leagues, had_splits = booster_filters[booster.pk]
        lost_leagues = had_leagues and not booster.leagues.exists()
        lost_splits = had_splits and not booster.splits.exists()
        if not (lost_leagues or lost_splits):
            continue
        if booster.auto:
            deleted_boosters.append(booster.name)
            booster.delete()
        elif booster.is_active:
            booster.is_active = False
            booster.save(update_fields=["is_active"])
            disabled_boosters.append(booster.name)

    orphans = Player.objects.filter(pk__in=players, appearances__isnull=True, cards__isnull=True)
    for image in PlayerImage.objects.filter(player__in=orphans).exclude(file=""):
        image.file.delete(save=False)
    summary["players"] = orphans.count()
    orphans.delete()

    for roster in Roster.objects.filter(pk__in=[r.pk for r in rosters]):
        refresh_roster(roster)

    summary["deleted_boosters"] = deleted_boosters
    summary["disabled_boosters"] = disabled_boosters
    catalog.bump()
    return summary


def describe_removal(name: str, summary: dict) -> str:
    parts = [
        f"{name} : {summary['splits']} split(s), {summary['cards']} carte(s) "
        f"dont {summary['owned']} exemplaire(s) possédé(s), "
        f"{summary['players']} joueur(s) pro retirés"
    ]
    if summary["rosters"]:
        parts.append(f"{summary['rosters']} équipe(s) à compléter")
    if summary["deleted_boosters"]:
        parts.append("boosters supprimés : " + ", ".join(summary["deleted_boosters"]))
    if summary["disabled_boosters"]:
        parts.append("boosters désactivés : " + ", ".join(summary["disabled_boosters"]))
    return " ; ".join(parts) + "."


# --------------------------------------------------------------------- Regroupement des périodes
def regroup_splits(log=None) -> list[str]:
    """Regroupe les phases d'une ligue locale par année (LCK Cup, Rounds,
    playoffs → « LCK 2026 »). Les compétitions internationales restent à
    part. Les exemplaires possédés, les ouvertures et les équipes suivent :
    l'exemplaire d'un joueur sur une ancienne phase devient un exemplaire
    de sa carte de l'année, à la même rareté.

    Renvoie le nom des périodes créées ou complétées."""
    from esport.importer import LeaguepediaImporter, domestic_split_name, is_international
    from esport.models import Tournament

    from .models import RosterSlot

    log = log or (lambda message: None)
    groups: dict[tuple, list] = {}
    for tournament in Tournament.objects.filter(split__isnull=False).select_related("league", "split"):
        if is_international(tournament.league.region):
            continue
        year = tournament.year or tournament.split.year
        groups.setdefault((tournament.league_id, year), []).append(tournament)

    done = []
    for (_league_id, year), tournaments in sorted(groups.items(), key=lambda g: g[0][1]):
        league = tournaments[0].league
        name = domestic_split_name(league.short_name or league.name, year)
        old_splits = {t.split for t in tournaments}
        if {s.name for s in old_splits} == {name}:
            continue
        with transaction.atomic():
            target, _ = Split.objects.get_or_create(
                name=name,
                defaults={"league": league, "slug": slugify(name)[:200], "year": year, "split_label": "Saison",
                          "is_playable": any(s.is_playable for s in old_splits)},
            )
            starts = [d for s in old_splits for d in (s.start_date,) if d]
            ends = [d for s in old_splits for d in (s.end_date,) if d]
            target.start_date = min(starts + ([target.start_date] if target.start_date else []), default=None)
            target.end_date = max(ends + ([target.end_date] if target.end_date else []), default=None)
            target.last_imported_at = max((s.last_imported_at for s in old_splits if s.last_imported_at), default=None)
            target.save()
            Tournament.objects.filter(pk__in=[t.pk for t in tournaments]).update(split=target)

            LeaguepediaImporter.compute_split_stats(target)
            generate_cards(target)
            new_cards = {c.player_id: c for c in Card.objects.filter(split=target)}

            for old in old_splits - {target}:
                for item in UserCard.objects.filter(card__split=old).select_related("card"):
                    card = new_cards.get(item.card.player_id)
                    if card:
                        add_copies(item.user, card.pk, item.rarity_id, item.quantity)
                for pull in PackOpeningCard.objects.filter(card__split=old).select_related("card"):
                    card = new_cards.get(pull.card.player_id)
                    if card:
                        pull.card = card
                        pull.save(update_fields=["card"])
                for slot in RosterSlot.objects.filter(card__split=old).select_related("card"):
                    card = new_cards.get(slot.card.player_id)
                    if card:
                        slot.card = card
                        slot.save(update_fields=["card"])
                for booster in Booster.objects.filter(splits=old):
                    booster.splits.add(target)
                log(f"{old.name} → {target.name}")
                old.delete()
            refresh_rosters_of_split(target)
        done.append(target.name)
    catalog.bump()
    return done

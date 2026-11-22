"""L'équipe de la semaine et sa méta.

Chaque lundi à 00:00 (heure de Paris), une nouvelle méta : un bonus par
poste, chacun d'un type différent tiré au sort parmi KINDS. Le bonus
s'ajoute au bonus de rareté de l'exemplaire (en %) :

- les bonus « carte » (région, époque, équipe…) valent pour les 5
  champions du poste quand la carte remplit la condition ;
- les bonus « champion » (peu joué, classe) valent champion par champion.

Au début de la semaine, l'équipe de la semaine de chacun reprend sa
meilleure équipe permanente ; à lui de l'adapter à la méta.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta

from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Count
from django.utils import timezone

from esport.models import Champion, GameAppearance
from esport.prestige import INTERNATIONAL_SHORT_NAMES

from .models import ROLES, Card, Roster, WeeklyMeta

#: type → (bonus en %, bonus sur la carte (True) ou sur chaque champion (False)).
KINDS = {
    "region": (15, True),
    "era": (15, True),
    "team": (20, True),
    "international": (15, True),
    "country": (15, True),
    "underdog": (20, True),
    "offmeta": (25, False),
    "class": (15, False),
}

#: Outsider : la carte doit être notée au plus ceci.
UNDERDOG_MAX = 74
#: Peu joué : bonus maximal à 0 partie, nul à partir de ce nombre de parties pro.
OFFMETA_GAMES = 1500

REGIONS = {
    "Corée": ["Korea"],
    "Chine": ["China"],
    "Europe": ["Europe", "EMEA"],
    "Amérique du Nord": ["North America"],
    "Brésil": ["Brazil"],
    "Viêt Nam": ["Vietnam"],
    "Japon": ["Japan"],
    "Asie-Pacifique": ["PCS", "LMS", "Asia Pacific", "APAC", "Taiwan"],
    "Amérique latine": ["Latin America", "LATAM"],
    "Océanie": ["Oceania"],
    "Turquie": ["Turkey"],
    "CEI": ["CIS"],
}

COUNTRIES = {
    "South Korea": "Corée du Sud", "Korea": "Corée du Sud", "China": "Chine", "France": "France",
    "Germany": "Allemagne", "Spain": "Espagne", "Denmark": "Danemark", "Sweden": "Suède",
    "Poland": "Pologne", "United States": "États-Unis", "Canada": "Canada", "Brazil": "Brésil",
    "Vietnam": "Viêt Nam", "Taiwan": "Taïwan", "Japan": "Japon", "Belgium": "Belgique",
    "Netherlands": "Pays-Bas", "United Kingdom": "Royaume-Uni", "Czech Republic": "Tchéquie",
    "Czechia": "Tchéquie", "Turkey": "Turquie", "Slovenia": "Slovénie", "Greece": "Grèce",
    "Italy": "Italie", "Portugal": "Portugal", "Australia": "Australie", "Russia": "Russie",
    "Croatia": "Croatie", "Lithuania": "Lituanie", "Romania": "Roumanie", "Hungary": "Hongrie",
    "Bulgaria": "Bulgarie", "Norway": "Norvège", "Finland": "Finlande", "Hong Kong": "Hong Kong",
    "Malaysia": "Malaisie", "Philippines": "Philippines", "Argentina": "Argentine", "Chile": "Chili",
    "Mexico": "Mexique", "Peru": "Pérou", "Slovakia": "Slovaquie", "Switzerland": "Suisse",
    "Austria": "Autriche", "Serbia": "Serbie", "Ukraine": "Ukraine", "Israel": "Israël",
}

CLASSES = {"Assassin": "Assassin", "Fighter": "Combattant", "Mage": "Mage", "Marksman": "Tireur",
           "Support": "Support", "Tank": "Tank"}
#: La classe évidente de chaque poste n'est pas tirée : ce ne serait pas un choix.
OBVIOUS_CLASS = {"Top": "Fighter", "Jungle": "Fighter", "Mid": "Mage", "Bot": "Marksman", "Support": "Support"}


# --------------------------------------------------------------------- Semaines
def week_start(now: datetime | None = None) -> date:
    """Le lundi (heure de Paris) de la semaine en cours."""
    local = timezone.localtime(now or timezone.now())
    return (local - timedelta(days=local.weekday())).date()


def week_end(week: date) -> datetime:
    """Le lundi suivant à 00:00, heure de Paris."""
    return timezone.make_aware(datetime.combine(week + timedelta(days=7), time.min))


# --------------------------------------------------------------------- Méta
def _pick(rng: random.Random, counts: dict, minimum: int):
    """Une valeur au hasard parmi celles qui ont au moins `minimum` cartes,
    pondérée par la racine du nombre de cartes (les grosses valeurs sortent
    plus souvent sans écraser les autres)."""
    options = sorted((k, n) for k, n in counts.items() if k and n >= minimum)
    if not options:
        return None
    keys = [k for k, _ in options]
    return rng.choices(keys, weights=[math.sqrt(n) for _, n in options])[0]


def _rule(kind: str, rng: random.Random, role: str, cards: list[dict], relaxed: bool = False) -> dict | None:
    """La règle d'un type pour un poste, ou None si aucune carte ne peut la
    remplir (relaxed : une seule carte suffit)."""
    bonus = KINDS[kind][0]
    many, few = (1, 1) if relaxed else (10, 3)
    if kind == "region":
        counts = {}
        for label, names in REGIONS.items():
            counts[label] = sum(1 for c in cards if c["region"] in names)
        label = _pick(rng, counts, many)
        if label is None:
            return None
        return {"value": REGIONS[label], "label": f"Joueur d'une ligue de la région {label}"}
    if kind == "era":
        counts = {}
        for c in cards:
            counts[c["year"]] = counts.get(c["year"], 0) + 1
        year = _pick(rng, counts, many)
        if year is None:
            return None
        return {"value": [year - 1, year + 1], "label": f"Carte des saisons {year - 1} à {year + 1}"}
    if kind == "team":
        counts, names = {}, {}
        for c in cards:
            counts[c["team"]] = counts.get(c["team"], 0) + 1
            names[c["team"]] = c["team_name"]
        team = _pick(rng, counts, few)
        if team is None:
            return None
        return {"value": team, "label": f"Joueur de {names[team]} (toutes années)"}
    if kind == "international":
        if not any(c["international"] for c in cards):
            return None
        return {"value": True, "label": "Carte d'une compétition internationale (Worlds, MSI, First Stand, EWC)"}
    if kind == "country":
        counts = {}
        for c in cards:
            if c["country"] in COUNTRIES:
                counts[c["country"]] = counts.get(c["country"], 0) + 1
        country = _pick(rng, counts, 1 if relaxed else 5)
        if country is None:
            return None
        same = sorted(k for k, v in COUNTRIES.items() if v == COUNTRIES[country])
        return {"value": same, "label": f"Joueur de nationalité {COUNTRIES[country]}"}
    if kind == "underdog":
        if not any(c["overall"] <= UNDERDOG_MAX for c in cards):
            return None
        return {"value": UNDERDOG_MAX, "label": f"Outsider : carte notée {UNDERDOG_MAX} ou moins"}
    if kind == "offmeta":
        return {"value": OFFMETA_GAMES,
                "label": "Champions peu joués en compétition : moins il est joué, plus le bonus est fort"}
    if kind == "class":
        tags = sorted(t for t in CLASSES if t != OBVIOUS_CLASS.get(role))
        tag = rng.choice(tags)
        return {"value": tag, "label": f"Champions de la classe {CLASSES[tag]}"}
    return None


def _role_cards(role: str) -> list[dict]:
    rows = Card.objects.filter(role=role, is_active=True).values(
        "overall", "team_id", "team__name", "player__country", "split__year",
        "split__league__region", "split__league__short_name",
    )
    return [
        {
            "overall": r["overall"] or 0,
            "team": r["team_id"],
            "team_name": r["team__name"],
            "country": (r["player__country"] or "").strip(),
            "year": r["split__year"],
            "region": (r["split__league__region"] or "").strip(),
            "international": (r["split__league__short_name"] or "").upper() in INTERNATIONAL_SHORT_NAMES
            or (r["split__league__region"] or "").strip().lower() == "international",
        }
        for r in rows
    ]


def generate_rules(week: date) -> dict:
    """5 bonus de 5 types différents, un par poste, tirés au sort (le même
    tirage pour une semaine donnée)."""
    rng = random.Random(f"meta-{week.isoformat()}")
    kinds = list(KINDS)
    rng.shuffle(kinds)
    rules = {}
    for role in ROLES:
        cards = _role_cards(role)
        # D'abord un type pas encore pris avec assez de cartes, puis avec
        # au moins une carte, enfin (très petite base) un bonus de champion.
        attempts = [(kinds, False), (kinds, True), (["offmeta", "class"], False)]
        for candidates, relaxed in attempts:
            rule = None
            for kind in list(candidates):
                rule = _rule(kind, rng, role, cards, relaxed)
                if rule is not None:
                    if kind in kinds:
                        kinds.remove(kind)
                    rules[role] = {"kind": kind, "bonus": KINDS[kind][0], "on_card": KINDS[kind][1], **rule}
                    break
            if rule is not None:
                break
    return rules


def meta_for(week: date | None = None) -> WeeklyMeta:
    week = week or week_start()
    meta = WeeklyMeta.objects.filter(week=week).first()
    if meta is None:
        try:
            meta = WeeklyMeta.objects.create(week=week, rules=generate_rules(week))
        except IntegrityError:
            meta = WeeklyMeta.objects.get(week=week)
    return meta


# --------------------------------------------------------------------- Bonus
def champion_games() -> dict[str, int]:
    """Parties pro jouées par champion (par nom), toutes années : gardé 6 h."""
    counts = cache.get("weekly:champion-games")
    if counts is None:
        counts = dict(
            GameAppearance.objects.exclude(champion="").order_by()
            .values_list("champion").annotate(n=Count("id")).values_list("champion", "n")
        )
        cache.set("weekly:champion-games", counts, 6 * 3600)
    return counts


def offmeta_bonus(games: int, maximum: int = KINDS["offmeta"][0]) -> int:
    """De `maximum` % (jamais joué) à 0 % (OFFMETA_GAMES parties ou plus)."""
    share = min(1.0, math.log1p(max(0, games)) / math.log1p(OFFMETA_GAMES))
    return round(maximum * (1 - share))


def card_bonus(rule: dict | None, card) -> int:
    """Le bonus (en %) que la règle d'un poste donne à une carte, pour tous ses champions."""
    if not rule or not rule.get("on_card"):
        return 0
    kind, value = rule["kind"], rule["value"]
    split = card.split
    league = split.league
    if kind == "region":
        ok = (league.region or "").strip() in value
    elif kind == "era":
        ok = value[0] <= split.year <= value[1]
    elif kind == "team":
        ok = card.team_id == value
    elif kind == "international":
        ok = (league.short_name or "").upper() in INTERNATIONAL_SHORT_NAMES or \
            (league.region or "").strip().lower() == "international"
    elif kind == "country":
        ok = (card.player.country or "").strip() in value
    elif kind == "underdog":
        ok = (card.overall or 0) <= value
    else:
        ok = False
    return rule["bonus"] if ok else 0


def champion_bonus(rule: dict | None, champion, games: dict[str, int] | None = None) -> int:
    """Le bonus (en %) que la règle d'un poste donne à un champion."""
    if not rule or rule.get("on_card"):
        return 0
    if rule["kind"] == "offmeta":
        games = champion_games() if games is None else games
        return offmeta_bonus(games.get(champion.name, 0), rule["bonus"])
    if rule["kind"] == "class":
        return rule["bonus"] if rule["value"] in (champion.tags or []) else 0
    return 0


def champion_bonuses(rules: dict) -> dict[str, dict[int, int]]:
    """{rôle: {id de champion: bonus}} pour les règles « champion » (le constructeur)."""
    out = {}
    champion_rules = {role: r for role, r in rules.items() if not r.get("on_card")}
    if not champion_rules:
        return out
    games = champion_games()
    champions = list(Champion.objects.only("id", "name", "tags"))
    for role, rule in champion_rules.items():
        out[role] = {c.pk: b for c in champions if (b := champion_bonus(rule, c, games))}
    return out


# --------------------------------------------------------------------- Équipes de la semaine
WEEKLY_NAME = "Équipe de la semaine"


def weekly_roster(user, week: date | None = None) -> Roster | None:
    """L'équipe de la semaine du joueur, créée au besoin à partir de sa
    meilleure équipe permanente complète."""
    week = week or week_start()
    roster = Roster.objects.filter(user=user, week=week).first()
    if roster is None:
        roster = copy_best_roster(user, week)
    return roster


def copy_best_roster(user, week: date, roster: Roster | None = None) -> Roster | None:
    """Reprend la meilleure équipe permanente (dans `roster` s'il est donné)."""
    from .services import GameError, roster_picks, save_roster

    best = Roster.objects.filter(user=user, week__isnull=True, score__isnull=False).order_by("-score").first()
    if best is None:
        return roster
    try:
        return save_roster(user, roster.name if roster else WEEKLY_NAME, roster_picks(best), roster, week=week)
    except (GameError, IntegrityError):
        return Roster.objects.filter(user=user, week=week).first()


def ensure_weekly_rosters(week: date | None = None) -> int:
    """Une équipe de la semaine pour chaque joueur qui a une équipe
    permanente complète (pour le classement de la semaine)."""
    week = week or week_start()
    users = (
        Roster.objects.filter(week__isnull=True, score__isnull=False)
        .exclude(user__rosters__week=week)
        .values_list("user", flat=True).distinct()
    )
    from django.contrib.auth import get_user_model

    created = 0
    for user in get_user_model().objects.filter(pk__in=list(users)):
        if copy_best_roster(user, week) is not None:
            created += 1
    return created

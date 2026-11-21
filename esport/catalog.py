"""Le catalogue du jeu : les ligues que l'import complet va chercher, et
celles du booster de base.

Les noms Leaguepedia ont changé avec les années (EU LCS → LEC, NA LCS →
LCS) : on liste les noms courts et complets connus, l'import prend ceux
qui existent.
"""

from __future__ import annotations

from django.db.models import Q

#: Les quatre ligues majeures du booster de base, sous tous leurs noms.
MAJOR_LEAGUES = {
    "LEC": ("LEC", "EU LCS", "LoL EMEA Championship", "Europe League Championship Series"),
    "LCK": ("LCK", "LoL Champions Korea", "Champions"),
    "LPL": ("LPL", "Tencent LoL Pro League"),
    "LCS": ("LCS", "NA LCS", "League Championship Series", "League of Legends Championship Series", "North America League Championship Series"),
}

#: Les compétitions internationales de Riot qui qualifient une équipe pour
#: le booster de base.
RIOT_EVENTS = ("MSI", "WCS", "Worlds", "FST", "Mid-Season Invitational", "World Championship", "First Stand")

#: L'Esports World Cup (son propre booster).
EWC = ("EWC", "Esports World Cup")

#: Tout ce que l'import complet va chercher, toutes années confondues.
FULL_IMPORT = tuple(n for names in MAJOR_LEAGUES.values() for n in names) + RIOT_EVENTS + EWC


def _matching(names) -> Q:
    shorts = {n.upper() for n in names}
    q = Q()
    for name in names:
        q |= Q(name__iexact=name)
    return q | Q(short_name__in=shorts) | Q(short_name__in=list(names))


def major_leagues_q() -> Q:
    return _matching([n for names in MAJOR_LEAGUES.values() for n in names])


def riot_events_q() -> Q:
    return _matching(RIOT_EVENTS) | Q(name__icontains="mid-season invitational") | Q(
        name__icontains="world championship"
    ) | Q(name__icontains="first stand")


def ewc_q() -> Q:
    return _matching(EWC) | Q(name__icontains="esports world cup")


def full_import_q() -> Q:
    return major_leagues_q() | riot_events_q() | ewc_q()


#: Des tournois rangés sous une compétition internationale qui n'en sont
#: pas : les finales régionales (qualifications) et les matchs d'exhibition.
SIDE_EVENT_WORDS = ("regional finals", "showmatch", "qualifier")


def is_side_event(name: str) -> bool:
    name = (name or "").lower()
    return any(word in name for word in SIDE_EVENT_WORDS)


def side_events_q(prefix: str = "") -> Q:
    q = Q()
    for word in SIDE_EVENT_WORDS:
        q |= Q(**{f"{prefix}name__icontains": word})
    return q

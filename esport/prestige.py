"""Le prestige d'une ligue : le niveau de son joueur moyen.

Les notes de parties comparent un joueur aux autres pros du même rôle,
toutes ligues confondues : dominer une ligue régionale ou de
développement pèserait autant que dominer la LCK. Le prestige fixe donc
le point de départ de la ligue : un joueur moyen de LCK (1,00) a une
carte à ~80, un joueur moyen de LEC (0,92) ~72. Chaque centième de
prestige en moins retire ~1 point à la carte (scoring.PRESTIGE_POINTS).
Les compétitions internationales valent 1 : tout le monde y affronte
les meilleurs.

Les valeurs par défaut sont calées sur les notes de dpm.lol (premier
split 2026) : la LCK, puis la LPL ~3 points derrière, la LEC ~8, la LCS ~11.

Chaque ligue peut avoir son propre coefficient dans la gestion (Ligues ›
Prestige) ; sinon celui de ce tableau, puis selon son niveau.
"""

from __future__ import annotations

#: Par nom court Leaguepedia (en majuscules).
DEFAULT_PRESTIGE = {
    # Les deux ligues les plus relevées.
    "LCK": 1.00, "LTC": 1.00,  # LTC : la LCK de 2012-2015 (OGN Champions)
    "LPL": 0.97,
    # Les autres ligues majeures.
    "LEC": 0.92,
    "LCS": 0.89, "LTA": 0.88, "LTA N": 0.89, "LTA NORTH": 0.89, "LTA S": 0.85, "LTA SOUTH": 0.85,
    "LCP": 0.87, "PCS": 0.86, "VCS": 0.86, "CBLOL": 0.85, "LJL": 0.84, "LLA": 0.83, "TCL": 0.83,
    "LCO": 0.82,
    # Les ligues de développement.
    "LCK CL": 0.85, "LDL": 0.84, "LFL": 0.80, "PRM": 0.79, "SL": 0.79, "NACL": 0.78,
}

#: Les compétitions internationales : aucune correction.
INTERNATIONAL_SHORT_NAMES = {"WCS", "WORLDS", "MSI", "FST", "EWC"}
INTERNATIONAL_PRESTIGE = 1.0

#: Une ligue absente du tableau, selon son niveau Leaguepedia.
LEVEL_PRESTIGE = {"primary": 0.85, "secondary": 0.80}
FALLBACK_PRESTIGE = 0.80

#: Les bornes d'un coefficient (aussi celles de la gestion).
MIN_PRESTIGE = 0.5
MAX_PRESTIGE = 1.5


def default_prestige(short_name: str = "", region: str = "", level: str = "") -> float:
    short = (short_name or "").strip().upper()
    if short in INTERNATIONAL_SHORT_NAMES or (region or "").strip().lower() == "international":
        return INTERNATIONAL_PRESTIGE
    if short in DEFAULT_PRESTIGE:
        return DEFAULT_PRESTIGE[short]
    return LEVEL_PRESTIGE.get((level or "").strip().lower(), FALLBACK_PRESTIGE)


def league_prestige(league) -> float:
    """Le coefficient d'une ligue : celui de la gestion, sinon le défaut."""
    if league is None:
        return 1.0
    if getattr(league, "prestige", None) is not None:
        return float(league.prestige)
    return default_prestige(league.short_name, league.region, league.level)


def league_base(prestige: float) -> float:
    """La note (0–100) du joueur moyen d'une ligue de ce prestige."""
    from .scoring import LEAGUE_BASE, PRESTIGE_POINTS

    return LEAGUE_BASE + PRESTIGE_POINTS * (prestige - 1)

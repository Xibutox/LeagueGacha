"""Le prestige d'une ligue : un coefficient qui multiplie la note des
joueurs qui y jouent.

Les notes de parties comparent un joueur aux autres pros du même rôle,
toutes ligues confondues : dominer une ligue régionale ou de
développement pèserait autant que dominer la LCK. Le coefficient corrige
ça : 1 pour la LCK, la LPL et les compétitions internationales (où tout le
monde affronte les meilleurs, donc sans correction), moins pour les autres.

Une note de split de 90 en ligue à 0,72 devient 65 ; la moyenne de la
ligue (50) devient 36.

Chaque ligue peut avoir son propre coefficient dans la gestion (Ligues ›
Prestige) ; sinon celui de ce tableau, puis selon son niveau.
"""

from __future__ import annotations

#: Par nom court Leaguepedia (en majuscules).
DEFAULT_PRESTIGE = {
    # Les deux ligues les plus relevées.
    "LCK": 1.00, "LPL": 1.00,
    # Les autres ligues majeures.
    "LEC": 0.92,
    "LCS": 0.85, "LTA": 0.85, "LTA N": 0.85, "LTA NORTH": 0.85, "LTA S": 0.80, "LTA SOUTH": 0.80,
    "LCP": 0.84, "PCS": 0.82, "VCS": 0.82, "CBLOL": 0.80, "LJL": 0.80, "LLA": 0.78, "TCL": 0.78,
    "LCO": 0.75,
    # Les ligues de développement.
    "LCK CL": 0.78, "LDL": 0.76, "LFL": 0.72, "PRM": 0.70, "SL": 0.70, "NACL": 0.68,
}

#: Les compétitions internationales : aucune correction.
INTERNATIONAL_SHORT_NAMES = {"WCS", "WORLDS", "MSI", "FST", "EWC"}
INTERNATIONAL_PRESTIGE = 1.0

#: Une ligue absente du tableau, selon son niveau Leaguepedia.
LEVEL_PRESTIGE = {"primary": 0.80, "secondary": 0.70}
FALLBACK_PRESTIGE = 0.70

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


#: Le point fixe de la correction sur une note de partie. Les notes de
#: split et de champion étirent la moyenne des parties autour de 50
#: (scoring.SPLIT_STRETCH) : corriger chaque partie autour de ce point
#: revient exactement à multiplier la note finale par le coefficient, et
#: l'historique d'un joueur passé d'une ligue à l'autre reste cohérent.
def _anchor() -> float:
    from .scoring import SPLIT_STRETCH

    return 50 - 50 / SPLIT_STRETCH


def apply_prestige(rating: float, prestige: float) -> float:
    """La note d'une partie (0–100) corrigée par le coefficient, bornée."""
    anchor = _anchor()
    return round(max(0.0, min(100.0, anchor + prestige * (rating - anchor))), 1)


def prestige_prior(prestige: float) -> float:
    """La moyenne de référence d'un split (le joueur moyen de la ligue),
    corrigée comme une partie."""
    return apply_prestige(50.0, prestige)

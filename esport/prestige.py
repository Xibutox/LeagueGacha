"""Le prestige d'une ligue : un coefficient qui multiplie la note de chaque
partie qui s'y joue.

Les notes de parties comparent un joueur aux autres pros du même rôle,
toutes ligues confondues : briller face à une opposition faible pèserait
autant que briller en LCK. Le coefficient corrige ça : 1 pour la
référence (LEC), plus haut pour les ligues les plus relevées et les
compétitions internationales, plus bas pour les ligues régionales.

Chaque ligue peut avoir son propre coefficient dans la gestion (Ligues ›
Prestige) ; sinon celui de ce tableau, puis selon son niveau.
"""

from __future__ import annotations

#: Par nom court Leaguepedia (en majuscules).
DEFAULT_PRESTIGE = {
    # Compétitions internationales : face aux meilleurs du monde.
    "WCS": 1.10, "WORLDS": 1.10, "MSI": 1.10, "FST": 1.08, "EWC": 1.06,
    # Les deux ligues majeures.
    "LCK": 1.06, "LPL": 1.06,
    # La référence.
    "LEC": 1.00,
    # Les autres ligues majeures.
    "LTA": 0.96, "LTA N": 0.96, "LTA NORTH": 0.96, "LTA S": 0.93, "LTA SOUTH": 0.93, "LCS": 0.97,
    "LCP": 0.95, "PCS": 0.93, "VCS": 0.93, "CBLOL": 0.92, "LJL": 0.92, "LLA": 0.90, "TCL": 0.90,
    "LCO": 0.88,
    # Les ligues de développement les plus relevées.
    "LCK CL": 0.92, "LDL": 0.92, "LFL": 0.92, "PRM": 0.90, "SL": 0.90, "NACL": 0.90,
}

#: Une ligue absente du tableau, selon son niveau Leaguepedia.
LEVEL_PRESTIGE = {"primary": 0.92, "secondary": 0.88}
INTERNATIONAL_PRESTIGE = 1.06
FALLBACK_PRESTIGE = 0.88

#: Les bornes d'un coefficient (aussi celles de la gestion).
MIN_PRESTIGE = 0.5
MAX_PRESTIGE = 1.5


def default_prestige(short_name: str = "", region: str = "", level: str = "") -> float:
    short = (short_name or "").strip().upper()
    if short in DEFAULT_PRESTIGE:
        return DEFAULT_PRESTIGE[short]
    if (region or "").strip().lower() == "international":
        return INTERNATIONAL_PRESTIGE
    return LEVEL_PRESTIGE.get((level or "").strip().lower(), FALLBACK_PRESTIGE)


def league_prestige(league) -> float:
    """Le coefficient d'une ligue : celui de la gestion, sinon le défaut."""
    if league is None:
        return 1.0
    if getattr(league, "prestige", None) is not None:
        return float(league.prestige)
    return default_prestige(league.short_name, league.region, league.level)


def apply_prestige(rating: float, prestige: float) -> float:
    """La note d'une partie (0–100) multipliée par le coefficient, bornée."""
    return round(max(0.0, min(100.0, rating * prestige)), 1)

"""Logique pure (sans Django) : choix de la photo, tirage d'un booster."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RarityRule:
    key: object  # id de la rareté
    order: int
    weight: float  # taux de tirage (relatif : ramené à 100 %)


# ---------------------------------------------------------------- Photo
def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t}


def image_score(tournament_name: str, team_name: str, split_name: str, league_short: str, year: int,
                card_team: str = "") -> float:
    """Pertinence d'une photo Leaguepedia pour une carte joueur × split.

    3 : le nom du tournoi de la photo contient tous les mots du split (« LEC 2025 Spring »)
    2 : même ligue et même année ; 1 : même année ; 0 : autre. +0.5 si même équipe.
    """
    t = _tokens(tournament_name)
    score = 0.0
    split_tokens = _tokens(split_name)
    year_str = str(year) if year else ""
    if split_tokens and split_tokens <= t:
        score = 3
    elif year_str in t and league_short and league_short.lower() in t:
        score = 2
    elif year_str and year_str in t:
        score = 1
    if card_team and team_name and card_team.lower() == team_name.lower():
        score += 0.5
    return score


def best_image(candidates: list[dict], split_name: str, league_short: str, year: int, card_team: str = ""):
    """candidates : [{"key", "tournament", "team", "year_hint"}]. Renvoie la clé de la meilleure photo
    ou None. En cas d'égalité, préfère la photo la plus récente (année la plus haute dans le nom)."""
    if not candidates:
        return None

    def sort_key(c):
        years = [int(y) for y in re.findall(r"20\d\d", c.get("tournament") or "")]
        recent = max(years) if years else 0
        # On évite les photos postérieures au split si une plus ancienne fait l'affaire.
        not_future = 0 if (year and recent > year) else 1
        return (image_score(c.get("tournament", ""), c.get("team", ""), split_name, league_short, year, card_team),
                not_future, recent)

    return max(candidates, key=sort_key)["key"]


# ---------------------------------------------------------------- Tirage
def _weighted_choice(items: list, weights: list[float], rng: random.Random):
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)
    x = rng.uniform(0, total)
    upto = 0.0
    for item, w in zip(items, weights):
        upto += w
        if x <= upto:
            return item
    return items[-1]


def draw_pack(
    cards: list,
    rules: list[RarityRule],
    n: int,
    guaranteed_order: int | None = None,
    rng: random.Random | None = None,
) -> list[tuple[object, object]]:
    """Tire n cartes. Chaque joueur existe à toutes les raretés : la rareté
    de chaque emplacement est tirée selon les taux, puis la carte au hasard
    dans tout le contenu du booster.

    cards : ids des cartes du booster
    rules : raretés (taux déjà ajustés au booster)
    guaranteed_order : la dernière carte est d'une rareté d'ordre ≥ à cette valeur

    Renvoie [(clé de rareté, id de carte)], sans la même carte deux fois
    tant que le contenu le permet."""
    rng = rng or random.SystemRandom()
    if not cards or not rules:
        return []
    picked: list[tuple[object, object]] = []
    used: set = set()
    for slot in range(n):
        candidates = rules
        if guaranteed_order is not None and slot == n - 1:
            candidates = [r for r in rules if r.order >= guaranteed_order] or rules
        rule = _weighted_choice(candidates, [r.weight for r in candidates], rng)
        fresh = [c for c in cards if c not in used]
        card = rng.choice(fresh or cards)
        used.add(card)
        picked.append((rule.key, card))
    return picked

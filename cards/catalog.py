"""Le catalogue en cache : ce qu'il y a dans chaque booster.

Le contenu d'un booster (ses cartes, ses taux) ne change qu'à l'import,
ou quand l'admin modifie un booster, une rareté ou une carte. Le calculer
à chaque page (barre du haut, boutique, vitrine) coûtait plusieurs
requêtes lourdes ; on le garde en cache (Redis en production) sous un
numéro de version, que tout changement fait avancer.
"""

from __future__ import annotations

import uuid

from django.core.cache import cache

VERSION_KEY = "catalog:version"
TIMEOUT = 24 * 3600


def version() -> str:
    token = cache.get(VERSION_KEY)
    if token is None:
        token = uuid.uuid4().hex[:12]
        cache.set(VERSION_KEY, token, None)
    return token


def bump(*args, **kwargs) -> None:
    """Le catalogue a changé : tout est recalculé à la prochaine demande."""
    cache.set(VERSION_KEY, uuid.uuid4().hex[:12], None)


def _cached(name: str, booster_id: int, compute):
    key = f"catalog:{version()}:{name}:{booster_id}"
    value = cache.get(key)
    if value is None:
        value = compute()
        cache.set(key, value, TIMEOUT)
    return value


def pool_ids(booster) -> list[int]:
    """Les identifiants des cartes que ce booster peut donner."""
    return _cached("pool", booster.pk, lambda: list(booster.card_pool().values_list("pk", flat=True)))


def has_cards(booster) -> bool:
    return bool(pool_ids(booster))


def rules(booster):
    """Les taux de tirage du booster (cards.services.rarity_rules)."""
    from .services import rarity_rules

    return _cached("rules", booster.pk, lambda: rarity_rules(booster))


def connect_signals() -> None:
    """Tout ce qui change le contenu ou les taux d'un booster fait avancer
    la version. (Les imports, qui écrivent en masse sans signaux, appellent
    bump() eux-mêmes.)"""
    from django.db.models.signals import m2m_changed, post_delete, post_save

    from esport.models import League, Split

    from .models import Booster, BoosterRarityWeight, Card, Rarity

    for model in (Booster, BoosterRarityWeight, Card, Rarity, Split, League):
        post_save.connect(bump, sender=model, dispatch_uid=f"catalog-save-{model.__name__}")
        post_delete.connect(bump, sender=model, dispatch_uid=f"catalog-delete-{model.__name__}")
    for through in (Booster.leagues.through, Booster.splits.through):
        m2m_changed.connect(bump, sender=through, dispatch_uid=f"catalog-m2m-{through.__name__}")

"""Chaque page et chaque endpoint de la gestion, ouverts trois fois
(superutilisateur, sans permission, déconnecté) : generic.testing."""

from datetime import timedelta

import pytest

from cards.models import (
    Booster,
    BoosterGift,
    BoosterRarityWeight,
    BoosterStock,
    Card,
    PackOpening,
    PackOpeningCard,
    Portal,
    Rarity,
    UserCard,
)
from cards.services import open_booster, save_roster
from esport.models import (
    GameAppearance,
    League,
    Player,
    PlayerImage,
    PlayerSplitStats,
    Tournament,
)
from generic.testing import PageSweep


class TestEveryPage(PageSweep):
    @pytest.fixture
    def records(self, split, admin_user):
        booster = Booster.objects.create(name="Tous les splits")
        weight = BoosterRarityWeight.objects.create(
            booster=booster, rarity=Rarity.objects.first(), rate=10
        )
        opening = open_booster(admin_user, booster)
        card = Card.objects.first()
        UserCard.objects.get_or_create(user=admin_user, card=card, rarity=Rarity.objects.first())
        affinity = card.champions.first()
        roster = save_roster(
            admin_user, "Équipe test", {card.role: {"card": card.pk, "champions": [affinity.champion_id]}}
        )
        return {
            "esport.league": League.objects.first(),
            "esport.split": split,
            "esport.tournament": Tournament.objects.first(),
            "esport.team": card.team,
            "esport.player": card.player,
            "esport.playerimage": PlayerImage.objects.first(),
            "esport.gameappearance": GameAppearance.objects.first(),
            "esport.playersplitstats": PlayerSplitStats.objects.first(),
            "cards.rarity": Rarity.objects.first(),
            "cards.card": card,
            "cards.booster": booster,
            "cards.boosterrarityweight": weight,
            "cards.usercard": UserCard.objects.first(),
            "cards.packopening": opening,
            "cards.packopeningcard": PackOpeningCard.objects.first(),
            "cards.roster": roster,
            "cards.cardchampion": affinity,
            "esport.champion": affinity.champion,
            "cards.rosterslot": roster.slots.first(),
            "cards.rosterpick": roster.slots.first().picks.first(),
            "cards.boostergift": BoosterGift.objects.create(user=admin_user, booster=booster, quantity=2),
            "cards.portal": Portal.objects.create(
                booster=booster, starts_at=opening.opened_at, ends_at=opening.opened_at + timedelta(days=1)),
            "cards.boosterstock": BoosterStock.objects.get_or_create(
                user=admin_user, booster=booster, defaults={"refilled_at": opening.opened_at})[0],
        }

    #: Les écrans en lecture seule n'ont pas de formulaire d'ajout.
    expected = {
        "site:esport_gameappearance_add": (403,),
        "site:esport_playersplitstats_add": (403,),
        "site:cards_packopening_add": (403,),
        "site:cards_packopeningcard_add": (403,),
        "site:cards_cardchampion_add": (403,),
        # Un cadeau se fait par « Offrir des boosters », pas par un ajout.
        "site:cards_boostergift_add": (403,),
        # Fusionner, échanger, l'aperçu d'une équipe : en POST.
        "cards:fuse": (405,),
        "cards:exchange": (405,),
        "cards:open_portal": (405,),
        "cards:roster_preview": (405,),
        # Ouvrir un booster se fait en POST, se déconnecter aussi.
        "cards:open_booster": (405,),
        "logout": (405,),
        # Déjà connecté : l'inscription renvoie au jeu.
        "signup": (302,),
        # Une ouverture n'est visible que de celui qui l'a faite.
        "cards:opening": (404,),
        # Une équipe n'est visible que de son propriétaire ; supprimer = POST.
        "cards:roster_edit": (404,),
        "cards:roster_delete": (405,),
    }

    def value_for(self, url_name, argument, records):
        # Les pages du jeu.
        if url_name == "cards:open_booster":
            return records["cards.booster"].slug
        if url_name == "cards:opening":
            return records["cards.packopening"].pk
        if url_name == "cards:open_portal":
            return records["cards.portal"].pk
        if url_name in {"cards:card_detail", "cards:fuse", "cards:exchange"}:
            return records["cards.card"].pk
        if url_name in {"cards:roster_edit", "cards:roster_delete"}:
            return records["cards.roster"].pk
        return super().value_for(url_name, argument, records)

"""Le jeu : cartes, boosters (réserve, tirage), collection (fusion,
échange), équipes (5 compositions, cohérence, bonus de rareté) et pages."""

import json
import random
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from cards.models import Booster, BoosterStock, Card, PackOpening, Rarity, Roster, RosterPick, UserCard
from cards.services import (
    GameError, available_packs, booster_status, exchange, fuse, generate_cards, open_booster, save_roster,
)

pytestmark = pytest.mark.django_db

ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"]


@pytest.fixture
def booster(split):
    return Booster.objects.create(name="Test", cards_per_pack=5, starting_stock=2, max_stock=3, cooldown_hours=1)


@pytest.fixture
def bob(db):
    return get_user_model().objects.create_user("bob", password="mdp-bob-123")


def rarity(code):
    return Rarity.objects.get(code=code)


def give(user, card, code="commune", quantity=1):
    return UserCard.objects.create(user=user, card=card, rarity=rarity(code), quantity=quantity)


# --------------------------------------------------------------------- Cartes
def test_each_player_of_the_split_gets_one_card(split):
    assert Card.objects.filter(split=split).count() == 10
    assert all(60 <= c.overall <= 99 for c in Card.objects.all())


def test_a_card_picks_the_photo_of_its_split(split):
    card = Card.objects.select_related("image").first()
    assert card.image.tournament_name == "LEC 2025 Spring"


def test_a_locked_photo_survives_a_recompute(split):
    card = Card.objects.first()
    card.image, card.image_locked = None, True
    card.save()

    generate_cards(split)

    card.refresh_from_db()
    assert card.image is None


def test_default_rarities_are_set_up_for_fusion_and_exchange(split):
    assert [r.code for r in Rarity.objects.all()] == ["commune", "rare", "epique", "legendaire"]
    assert rarity("commune").fusion_cost == 2
    assert rarity("legendaire").exchange_rarity == rarity("rare")
    assert float(rarity("legendaire").score_bonus) > float(rarity("rare").score_bonus) > 0


# --------------------------------------------------------------------- Boosters
def test_opening_a_booster_adds_copies_with_their_rarity(booster, bob):
    opening = open_booster(bob, booster, rng=random.Random(0))

    pulls = list(opening.cards.all())
    assert len(pulls) == 5
    assert all(p.rarity_id for p in pulls)
    assert sum(UserCard.objects.filter(user=bob).values_list("quantity", flat=True)) == 5
    for pull in pulls:
        assert UserCard.objects.filter(user=bob, card=pull.card, rarity=pull.rarity).exists()


def test_the_stock_runs_out_then_refills(booster, bob):
    open_booster(bob, booster, rng=random.Random(0))
    open_booster(bob, booster, rng=random.Random(1))

    with pytest.raises(GameError, match="prochain arrive dans"):
        open_booster(bob, booster)
    assert PackOpening.objects.filter(user=bob).count() == 2

    stock = BoosterStock.objects.get(user=bob, booster=booster)
    stock.refilled_at -= timedelta(hours=1, minutes=5)
    stock.save()
    assert available_packs(stock)[0] == 1
    open_booster(bob, booster)


def test_a_full_stock_loses_the_extra_boosters(booster, bob):
    stock = BoosterStock.objects.create(user=bob, booster=booster, stock=0,
                                        refilled_at=timezone.now() - timedelta(hours=10))
    available, next_at = available_packs(stock)
    assert (available, next_at) == (3, None)  # 10 gagnés, 3 gardés

    open_booster(bob, booster)

    stock.refresh_from_db()
    assert stock.stock == 2
    assert timezone.now() - stock.refilled_at < timedelta(minutes=1)


@override_settings(GAME_STAFF_UNLIMITED=True)
def test_staff_are_not_limited(booster, bob):
    bob.is_staff = True
    bob.save()

    for _ in range(5):
        open_booster(bob, booster)


def test_an_empty_booster_refuses_to_open(db, bob):
    empty = Booster.objects.create(name="Vide")

    with pytest.raises(GameError):
        open_booster(bob, empty)


def test_a_booster_filters_by_league_year_and_split(split):
    from esport.models import League

    other = League.objects.create(name="LoL Champions Korea", short_name="LCK", region="Korea")
    by_league = Booster.objects.create(name="LEC")
    by_league.leagues.add(split.league)
    assert by_league.card_pool().count() == 10
    by_other = Booster.objects.create(name="LCK")
    by_other.leagues.add(other)
    assert by_other.card_pool().count() == 0
    assert Booster.objects.create(name="2025", years="2025").card_pool().count() == 10
    assert Booster.objects.create(name="2024", years="2024, 2023").card_pool().count() == 0


def test_international_boosters_come_less_often(split):
    from cards.services import ensure_boosters
    from esport.models import League, Split

    msi = League.objects.create(name="Mid-Season Invitational", short_name="MSI", region="International")
    Split.objects.filter(pk=split.pk).update(league=msi)
    ensure_boosters()

    booster = Booster.objects.get(auto=True, leagues=msi, years="2025")
    assert booster.cooldown_hours > 1 and booster.max_stock < 24


# --------------------------------------------------------------------- Fusion et échange
def test_two_commons_fuse_into_a_rare(split, bob):
    card = Card.objects.first()
    give(bob, card, "commune", 3)

    assert fuse(bob, card.pk, rarity("commune").pk) == rarity("rare")

    assert UserCard.objects.get(user=bob, card=card, rarity__code="commune").quantity == 1
    assert UserCard.objects.get(user=bob, card=card, rarity__code="rare").quantity == 1
    with pytest.raises(GameError, match="Il te faut 2"):
        fuse(bob, card.pk, rarity("commune").pk)


def test_the_top_rarity_does_not_fuse(split, bob):
    card = Card.objects.first()
    give(bob, card, "legendaire", 3)

    with pytest.raises(GameError):
        fuse(bob, card.pk, rarity("legendaire").pk)


def test_a_legendary_duplicate_trades_for_a_rare_among_five_drawn_players(split, bob):
    from cards.services import choose_exchange, pending_exchanges

    card = Card.objects.first()
    give(bob, card, "legendaire", 2)

    offer = exchange(bob, card.pk, rarity("legendaire").pk)
    choices = list(offer.choices.all())
    assert len(choices) == 5 and len({c.player_id for c in choices}) == 5
    assert offer.rarity == rarity("rare") and list(pending_exchanges(bob)) == [offer]
    assert UserCard.objects.get(user=bob, card=card, rarity__code="legendaire").quantity == 1
    with pytest.raises(GameError):  # le dernier exemplaire reste
        exchange(bob, card.pk, rarity("legendaire").pk)

    outsider = Card.objects.exclude(pk__in=[c.pk for c in choices]).first()
    with pytest.raises(GameError, match="proposées"):
        choose_exchange(bob, offer.pk, outsider.pk)
    target = choices[2]
    assert choose_exchange(bob, offer.pk, target.pk) == (target, rarity("rare"))
    assert UserCard.objects.get(user=bob, card=target, rarity__code="rare").quantity == 1
    with pytest.raises(GameError, match="déjà choisi"):
        choose_exchange(bob, offer.pk, choices[0].pk)
    assert not pending_exchanges(bob).exists()


def test_fusion_and_exchange_from_the_card_page(client, split, bob):
    card = Card.objects.first()
    give(bob, card, "commune", 2)
    give(bob, card, "legendaire", 2)
    client.force_login(bob)

    page = client.get(reverse("cards:card_detail", args=[card.pk])).content.decode()
    assert "Fusionner 2 → 1 rare" in page and "au choix parmi 5" in page

    client.post(reverse("cards:fuse", args=[card.pk]), {"rarity": rarity("commune").pk})
    assert UserCard.objects.filter(user=bob, card=card, rarity__code="rare").exists()
    response = client.post(reverse("cards:exchange", args=[card.pk]), {"rarity": rarity("legendaire").pk})
    assert response.status_code == 302 and "/echange/" in response["Location"]

    offer_page = client.get(response["Location"])
    assert len(offer_page.context["choices"]) == 5
    assert "Échange en attente" in client.get(reverse("cards:home")).content.decode()
    # Recharger ne relance pas le tirage.
    assert offer_page.context["choices"] == client.get(response["Location"]).context["choices"]

    target = offer_page.context["choices"][0]
    choose_url = reverse("cards:choose_exchange", args=[offer_page.context["offer"].pk])
    assert client.post(choose_url, {"card": target.pk}).status_code == 302
    assert UserCard.objects.filter(user=bob, card=target, rarity__code="rare").exists()
    assert "Échange fait" in client.get(response["Location"]).content.decode()


# --------------------------------------------------------------------- Pages
def test_the_game_pages_answer(client, booster, bob):
    client.force_login(bob)

    assert client.get(reverse("cards:home")).status_code == 200
    response = client.post(reverse("cards:open_booster", args=[booster.slug]))
    assert response.status_code == 302
    assert client.get(response["Location"]).status_code == 200
    collection = reverse("cards:collection")
    for query in ("", "?possession=manquantes", "?possession=fusion", "?rarete=rare", "?role=Mid"):
        assert client.get(collection + query).status_code == 200
    card = Card.objects.first()
    assert client.get(reverse("cards:card_detail", args=[card.pk])).status_code == 200


def test_the_home_page_is_public(client, booster):
    assert client.get(reverse("cards:home")).status_code == 200


def test_signing_up_logs_in(client, db):
    response = client.post(
        reverse("signup"),
        {"username": "alice", "password1": "un-mot-de-passe-solide", "password2": "un-mot-de-passe-solide"},
    )
    assert response.status_code == 302
    assert client.get(reverse("cards:collection")).status_code == 200


def test_a_fresh_opening_plays_the_animation_once(client, booster, bob):
    client.force_login(bob)
    response = client.post(reverse("cards:open_booster", args=[booster.slug]))
    page = client.get(response["Location"])

    assert page.context["animate"] is True
    assert 'id="pack"' in page.content.decode()
    assert page.context["new_count"] + page.context["duplicate_count"] == 5

    replay = client.get(response["Location"] + "?anim=0")
    assert replay.context["animate"] is False
    assert 'id="pack"' not in replay.content.decode()


def test_the_home_page_shows_the_stock_and_the_countdown(client, booster, bob):
    client.force_login(bob)

    page = client.get(reverse("cards:home")).content.decode()

    assert f'action="{reverse("cards:open_booster", args=[booster.slug])}"' in page
    assert "2 boosters t'attendent" in page
    assert "data-countdown" in page
    assert '<span class="tab-count"' in page


def test_the_flag_filter_turns_a_country_into_a_flag():
    from cards.templatetags.game import flag

    assert "flagcdn.com/kr.svg" in flag("South Korea")
    assert 'title="Corée du Sud"' in flag("South Korea")
    assert "gb-eng" in flag("England")
    assert flag("Atlantis") == "Atlantis"
    assert flag("") == ""


def test_a_card_shows_the_flag(client, booster, bob):
    card = Card.objects.first()
    give(bob, card)
    client.force_login(bob)
    page = client.get(reverse("cards:card_detail", args=[card.pk])).content.decode()
    assert "flagcdn.com/fr.svg" in page


# --------------------------------------------------------------------- Champions
def test_champion_keys_follow_riot():
    from esport.champions import champion_key

    assert champion_key("Kai'Sa") == "Kaisa"
    assert champion_key("Kog'Maw") == "KogMaw"
    assert champion_key("Wukong") == "MonkeyKing"
    assert champion_key("Lee Sin") == "LeeSin"
    assert champion_key("Dr. Mundo") == "DrMundo"
    assert champion_key("Ahri") == "Ahri"


def test_each_card_knows_its_champions(split):
    from cards.models import CardChampion

    card = Card.objects.get(player__ign="Player0")
    affinities = list(card.champions.all())
    assert {a.champion.name for a in affinities} == {"Ahri", "Azir", "Kai'Sa", "Wukong", "Lee Sin", "Nautilus"}
    assert sum(a.games for a in affinities) == 6
    assert all(0 <= a.rating <= 100 for a in affinities)
    assert CardChampion.objects.filter(champion__key="MonkeyKing").exists()


def test_past_games_on_a_champion_lift_its_rating(split):
    from cards.services import compute_card_champions
    from esport.models import GameAppearance, Player, Split, Tournament

    card = Card.objects.get(player__ign="Player0")
    before = card.champions.get(champion__name="Ahri")
    old = Split.objects.create(league=split.league, name="LEC 2024", year=2024)
    tournament = Tournament.objects.create(overview_page="LEC/2024", name="LEC 2024", league=split.league,
                                           split=old, year=2024)
    for g in range(5):
        GameAppearance.objects.create(game_id=f"OLD{g}", tournament=tournament, player=Player.objects.get(ign="Player0"),
                                      role="Top", champion="Ahri", win=True, rating=90)
    GameAppearance.objects.create(game_id="OLD9", tournament=tournament, player=Player.objects.get(ign="Player0"),
                                  role="Top", champion="Teemo", win=True, rating=90)

    compute_card_champions(split)

    after = card.champions.get(champion__name="Ahri")
    assert after.games == before.games and after.history_games == 5
    assert after.rating > before.rating
    # Un champion joué seulement avant reste sélectionnable, avec son historique.
    teemo = card.champions.get(champion__name="Teemo")
    assert (teemo.games, teemo.history_games) == (0, 1)


def test_data_dragon_fills_champion_profiles(split, tmp_path, settings):
    from esport.ddragon import sync_champions
    from esport.models import Champion

    settings.GAME_MEDIA_ROOT = tmp_path

    class FakeDragon:
        def latest_version(self):
            return "15.1.1"

        def champions(self, version, language="en_US"):
            title = "la Renarde à neuf queues" if language == "fr_FR" else "the Nine-Tailed Fox"
            return {
                "Ahri": {"name": "Ahri", "title": title, "tags": ["Mage", "Assassin"],
                         "info": {"attack": 3, "defense": 4, "magic": 8, "difficulty": 5}},
                "Teemo": {"name": "Teemo", "title": "", "tags": ["Marksman"], "info": {"attack": 5}},
            }

        def download(self, url):
            return b"\x89PNG"

    result = sync_champions(FakeDragon())

    ahri = Champion.objects.get(name="Ahri")
    assert (ahri.title, ahri.tags, ahri.magic) == ("la Renarde à neuf queues", ["Mage", "Assassin"], 8)
    assert ahri.portrait and ahri.icon and ahri.splash
    assert ahri.portrait_url.startswith("/game-media/champions/")
    assert result["created"] == 1  # Teemo
    assert "Kai'Sa" not in result["missing"] or True


# --------------------------------------------------------------------- Équipes
@pytest.fixture
def owned(split, bob):
    for card in Card.objects.all():
        give(bob, card)
    return {c.role: c for c in Card.objects.order_by("-overall")}


@pytest.fixture
def champion_ids(split):
    from esport.models import Champion

    return list(Champion.objects.order_by("name").values_list("pk", flat=True))


def team_picks(owned, champion_ids):
    """Un carré latin : 5 champions différents par joueur, et dans chaque
    composition 5 champions différents."""
    return {
        role: {"card": owned[role].pk,
               "champions": [champion_ids[(j + k) % len(champion_ids)] for k in range(5)]}
        for j, role in enumerate(ROLES)
    }


@pytest.fixture
def full_team(owned, champion_ids):
    return team_picks(owned, champion_ids)


def test_a_full_team_gets_a_score_from_its_five_compositions(bob, full_team):
    roster = save_roster(bob, "Ma team", full_team)

    assert RosterPick.objects.filter(slot__roster=roster).count() == 25
    comps = roster.details["compositions"]
    assert len(comps) == 5 and all(c["complete"] for c in comps)
    assert all("coherence" in c and 0 <= c["coherence"]["score"] <= 100 for c in comps)
    assert roster.score == round(sum(c["score"] for c in comps) / 5, 1)


def test_the_slot_score_uses_the_champion_rating_and_the_rarity_bonus(bob, full_team, owned):
    from cards.services import evaluate_roster
    from esport.scoring import slot_score

    plain = evaluate_roster(bob, full_team)
    give(bob, owned["Mid"], "legendaire")
    boosted = evaluate_roster(bob, full_team)

    mid = boosted["slots"]["Mid"]
    assert mid["rarity"]["name"] == "Légendaire" and mid["rarity"]["bonus"] == 20
    first = mid["champions"][0]
    card = owned["Mid"]
    assert first["score"] == slot_score(card.stats.score, first["rating"], 20)
    assert boosted["score"] > plain["score"]


def test_an_unplayed_champion_is_allowed_with_a_penalty(bob, owned):
    from cards.services import evaluate_roster
    from esport.models import Champion
    from esport.scoring import UNPLAYED_PENALTY

    teemo = Champion.objects.create(name="Teemo", key="Teemo")
    result = evaluate_roster(bob, {"Top": {"card": owned["Top"].pk, "champions": [teemo.pk]}}, check=True)

    entry = result["slots"]["Top"]["champions"][0]
    assert entry["played"] is False
    assert entry["rating"] == round(owned["Top"].stats.score - UNPLAYED_PENALTY, 1)


def test_a_team_without_all_champions_is_not_ranked(bob, owned):
    roster = save_roster(bob, "Ma team", {role: card.pk for role, card in owned.items()})
    assert roster.score is None
    assert roster.slots.count() == 5


def test_a_player_takes_five_different_champions(bob, owned, champion_ids):
    with pytest.raises(GameError, match="5 champions différents"):
        save_roster(bob, "x", {"Top": {"card": owned["Top"].pk, "champions": [champion_ids[0]] * 2}})


def test_a_champion_is_used_once_per_composition(bob, owned, champion_ids):
    picks = {"Top": {"card": owned["Top"].pk, "champions": [champion_ids[0]]},
             "Mid": {"card": owned["Mid"].pk, "champions": [champion_ids[0]]}}
    with pytest.raises(GameError, match="Composition 1"):
        save_roster(bob, "x", picks)


def test_a_roster_refuses_a_card_in_the_wrong_role(bob, owned):
    with pytest.raises(GameError):
        save_roster(bob, "Ma team", {"Top": owned["Mid"].pk})


def test_a_roster_refuses_a_card_not_owned(split, bob):
    card = Card.objects.get(role="Top", player__ign="Player0")
    with pytest.raises(GameError):
        save_roster(bob, "Ma team", {"Top": card.pk})


def test_fusing_refreshes_the_teams(bob, full_team, owned):
    roster = save_roster(bob, "Ma team", full_team)
    before = roster.score
    give(bob, owned["Bot"], "epique", 2)

    fuse(bob, owned["Bot"].pk, rarity("epique").pk)

    roster.refresh_from_db()
    assert roster.score > before


def test_the_ranking_keeps_each_users_best_team(client, bob, full_team, owned):
    from cards.services import leaderboard

    save_roster(bob, "Équipe A", full_team)
    save_roster(bob, "Équipe B", {"Top": full_team["Top"]})  # incomplète : pas classée
    alice = get_user_model().objects.create_user("alice", password="x-pass-123")
    for card in Card.objects.all():
        give(alice, card)
    save_roster(alice, "Alice team", full_team)

    rows = leaderboard()
    assert [r.user.username for _, r in rows].count("bob") == 1
    assert len(rows) == 2

    client.force_login(bob)
    page = client.get(reverse("cards:ranking")).content.decode()
    assert "Équipe A" in page and "Alice team" in page


def test_the_team_builder_previews_and_saves(client, bob, full_team):
    client.force_login(bob)
    new = reverse("cards:roster_new")

    page = client.get(new)
    assert page.status_code == 200
    data = json.loads(page.context["data"] and json.dumps(page.context["data"]))
    assert set(data["cards"]) == set(ROLES) and data["champions"]

    preview = client.post(reverse("cards:roster_preview"), json.dumps({"picks": full_team}),
                          content_type="application/json")
    assert preview.status_code == 200
    assert preview.json()["complete"] and preview.json()["score"] is not None

    # Régression : l'enregistrement part bien vers la page de l'équipe, pas vers l'aperçu.
    saved = client.post(new, {"name": "Les meilleurs", "picks": json.dumps(full_team)})
    assert saved.status_code == 302
    roster = Roster.objects.get(name="Les meilleurs")
    assert roster.score == preview.json()["score"]

    edit = client.get(reverse("cards:roster_edit", args=[roster.pk]))
    assert edit.context["data"]["picks"]["Mid"]["champions"] == full_team["Mid"]["champions"]
    assert "Les meilleurs" in client.get(reverse("cards:rosters")).content.decode()


def test_the_team_builder_keeps_the_choices_on_error(client, bob, full_team):
    client.force_login(bob)
    response = client.post(reverse("cards:roster_new"), {"name": "", "picks": json.dumps(full_team)})
    assert response.status_code == 200
    assert response.context["data"]["picks"]["Top"]["card"] == full_team["Top"]["card"]


# --------------------------------------------------------------------- Retrait d'une ligue
def test_removing_a_league_import_cleans_up(bob, full_team, split):
    from cards.services import ensure_boosters, remove_league_data
    from esport.models import GameAppearance, League, Player, Split

    league = split.league
    other = League.objects.create(name="LoL Champions Korea", short_name="LCK", region="Korea")
    ensure_boosters()
    auto = Booster.objects.get(slug="emea-2025")
    by_hand = Booster.objects.create(name="Ma ligue")
    by_hand.leagues.add(league)
    mixed = Booster.objects.create(name="Deux ligues")
    mixed.leagues.add(league, other)
    roster = save_roster(bob, "Ma team", full_team)

    summary = remove_league_data(league)

    assert summary["cards"] == 10 and summary["owned"] > 0 and summary["players"] == 10
    assert League.objects.filter(pk=league.pk).exists()
    assert not Split.objects.filter(league=league).exists()
    assert not GameAppearance.objects.exists() and not Player.objects.exists()
    assert not UserCard.objects.exists()
    assert not Booster.objects.filter(pk=auto.pk).exists()
    by_hand.refresh_from_db()
    assert not by_hand.is_active
    mixed.refresh_from_db()
    assert mixed.is_active and list(mixed.leagues.all()) == [other]
    assert Roster.objects.get(pk=roster.pk).score is None


def test_the_gestion_removes_a_league_import(admin_client, split):
    from esport.models import League

    response = admin_client.post(
        reverse("site:api_esport_league-actions"),
        {"action": "remove_import", "ids": [split.league_id]},
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    assert "10 carte(s)" in response.content.decode()
    assert not Card.objects.exists()
    assert League.objects.filter(pk=split.league_id).exists()


# --------------------------------------------------------------------- Regroupement des phases
def test_the_phases_of_a_year_are_grouped_and_collections_follow(bob, split):
    from cards.services import regroup_splits
    from esport.models import GameAppearance, Split, Tournament

    # Une deuxième phase de LEC 2025, avec les mêmes joueurs.
    summer = Split.objects.create(league=split.league, name="LEC 2025 Summer", year=2025)
    tournament = Tournament.objects.create(overview_page="LEC/2025 Summer", name="LEC 2025 Summer",
                                           league=split.league, split=summer, year=2025)
    for a in GameAppearance.objects.filter(tournament__split=split):
        a.pk, a.game_id, a.tournament = None, "S" + a.game_id, tournament
        a.save()
    from esport.importer import LeaguepediaImporter

    LeaguepediaImporter.compute_split_stats(summer)
    generate_cards(summer)
    spring_card = Card.objects.get(split=split, player__ign="Player0")
    summer_card = Card.objects.get(split=summer, player__ign="Player0")
    give(bob, spring_card, "rare")
    give(bob, summer_card, "rare")
    booster = Booster.objects.create(name="Printemps")
    booster.splits.add(split)

    assert regroup_splits() == ["LEC 2025"]

    year = Split.objects.get(name="LEC 2025")
    assert set(Split.objects.values_list("name", flat=True)) == {"LEC 2025"}
    assert year.tournaments.count() == 2
    card = Card.objects.get(split=year, player__ign="Player0")
    assert card.stats.games == 12
    assert UserCard.objects.get(user=bob, card=card, rarity__code="rare").quantity == 2
    assert list(booster.splits.all()) == [year]
    assert regroup_splits() == []  # déjà regroupé


def test_international_events_stay_apart(split):
    from esport.importer import LeaguepediaImporter

    assert LeaguepediaImporter.split_name_for(
        {"LeagueShort": "LCK", "Year": "2026", "Split": "Rounds 1-2", "Region": "Korea"}) == "LCK 2026"
    assert LeaguepediaImporter.split_name_for(
        {"LeagueShort": "MSI", "Year": "2025", "Split": "", "Name": "MSI 2025", "Region": "International"}) == "MSI 2025"


# --------------------------------------------------------------------- Visuel d'un booster
def _png():
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (50, 70), "#c8344b").save(buffer, "PNG")
    return buffer.getvalue()


def test_a_booster_shows_its_uploaded_visual(client, booster, bob, settings, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.GAME_MEDIA_ROOT = tmp_path
    booster.image = SimpleUploadedFile("coree.png", _png(), content_type="image/png")
    booster.full_clean()
    booster.save()
    client.force_login(bob)

    home = client.get(reverse("cards:home")).content.decode()
    assert "has-image" in home and booster.image.url in home
    opening = client.get(client.post(reverse("cards:open_booster", args=[booster.slug]))["Location"])
    assert booster.image.url in opening.content.decode()


def test_a_booster_visual_must_be_an_image(booster):
    from django.core.exceptions import ValidationError
    from django.core.files.uploadedfile import SimpleUploadedFile

    booster.image = SimpleUploadedFile("visuel.txt", b"pas une image", content_type="text/plain")
    with pytest.raises(ValidationError):
        booster.full_clean()


def test_the_gestion_uploads_a_booster_visual(admin_user, booster, settings, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from rest_framework.test import APIClient

    settings.GAME_MEDIA_ROOT = tmp_path
    client = APIClient()
    client.force_login(admin_user)

    response = client.patch(
        f"/gestion/api/cards/booster/{booster.pk}/",
        {"_payload": json.dumps({}), "image": SimpleUploadedFile("coree.png", _png(), content_type="image/png")},
        format="multipart",
    )

    assert response.status_code == 200, response.content
    booster.refresh_from_db()
    assert booster.image.name.startswith("boosters/coree")
    bad = client.patch(
        f"/gestion/api/cards/booster/{booster.pk}/",
        {"_payload": json.dumps({}), "image": SimpleUploadedFile("x.txt", b"texte", content_type="text/plain")},
        format="multipart",
    )
    assert bad.status_code == 400


# --------------------------------------------------------------------- Cadeaux de boosters
def test_gifted_boosters_add_up_beyond_the_maximum(booster, bob, admin_user):
    from cards.models import BoosterGift
    from cards.services import booster_status, give_boosters

    give_boosters([bob], booster, 10, given_by=admin_user, message="Bienvenue !")

    status = booster_status(bob, [booster])[booster.pk]
    assert status["available"] == 2 + 10 and status["gifted"] == 10  # 2 de départ, max 3
    gift = BoosterGift.objects.get(user=bob)
    assert (gift.quantity, gift.given_by, gift.message) == (10, admin_user, "Bienvenue !")


def test_the_regular_stock_is_used_before_the_gifts(booster, bob):
    from cards.services import give_boosters

    give_boosters([bob], booster, 1)
    open_booster(bob, booster)
    open_booster(bob, booster)
    stock = BoosterStock.objects.get(user=bob, booster=booster)
    assert (stock.stock, stock.bonus) == (0, 1)

    open_booster(bob, booster)  # la réserve est vide : le cadeau sert
    stock.refresh_from_db()
    assert stock.bonus == 0
    with pytest.raises(GameError):
        open_booster(bob, booster)


def test_a_gift_is_announced_once_on_the_home_page(client, booster, bob, admin_user):
    from cards.services import give_boosters

    give_boosters([bob], booster, 3, given_by=admin_user, message="Pour ton anniversaire")
    client.force_login(bob)

    page = client.get(reverse("cards:home")).content.decode()
    assert "Cadeau : 3 boosters Test" in page and "Pour ton anniversaire" in page
    assert "dont 3 offerts" in page
    assert "Cadeau :" not in client.get(reverse("cards:home")).content.decode()


def test_the_gestion_gives_boosters_to_chosen_players_or_everyone(admin_client, booster, bob):
    from cards.models import BoosterGift

    url = reverse("site:cards_booster_offrir")
    assert admin_client.get(url).status_code == 200

    response = admin_client.post(url, {"booster": booster.pk, "quantity": 2, "users": [bob.pk]},
                                 content_type="application/json")
    assert response.status_code == 200, response.content
    assert "offert(s) à 1 joueur(s)" in response.json()["_message"]

    response = admin_client.post(url, {"booster": booster.pk, "quantity": 1, "everyone": True},
                                 content_type="application/json")
    assert response.status_code == 200, response.content
    assert BoosterGift.objects.filter(user=bob).count() == 2

    refused = admin_client.post(url, {"booster": booster.pk, "quantity": 1}, content_type="application/json")
    assert refused.status_code == 400


# --------------------------------------------------------------------- Boosters types
def make_league(name, short, region):
    from esport.models import League, Split

    league = League.objects.create(name=name, short_name=short, region=region)
    Split.objects.create(league=league, name=f"{short} 2026", year=2026)
    return league


def test_the_booster_types(split):
    from cards.services import ensure_boosters

    lck = make_league("LoL Champions Korea", "LCK", "Korea")
    msi = make_league("Mid-Season Invitational", "MSI", "International")
    worlds = make_league("World Championship", "WCS", "International")
    first_stand = make_league("First Stand", "FST", "International")
    ewc = make_league("Esports World Cup", "EWC", "International")
    lfl = make_league("La Ligue Française", "LFL", "EMEA")

    created = ensure_boosters()

    assert {"Booster de base", "Esports World Cup", "EMEA 2025", "Corée 2026",
            "Riot International 2026"} <= set(created)
    base = Booster.objects.get(slug="base")
    assert base.is_active and base.qualified_teams_only
    assert set(base.leagues.all()) == {split.league, lck}  # pas la LFL
    every_year = Booster.objects.get(slug="ewc")
    assert set(every_year.leagues.all()) == {ewc} and not every_year.year_list()
    assert not every_year.is_active
    assert not Booster.objects.filter(slug="ewc-2026").exists()
    riot = Booster.objects.get(slug="riot-international-2026")
    assert set(riot.leagues.all()) == {msi, worlds, first_stand}
    assert riot.cooldown_hours > 1 and riot.max_stock < 24
    assert set(Booster.objects.get(slug="korea-2026").leagues.all()) == {lck}
    assert lfl in Booster.objects.get(slug="emea-2026").leagues.all()
    # Par région et par année : créés, mais éteints.
    assert not Booster.objects.filter(auto=True, is_active=True).exclude(slug="base").exists()
    assert ensure_boosters() == []  # relançable
    assert {b.guaranteed_rarity.code for b in Booster.objects.all()} == {"rare"}


def test_an_activated_booster_stays_active(split):
    from cards.services import ensure_boosters

    ensure_boosters(deactivate_obsolete=True)
    Booster.objects.filter(slug="emea-2025").update(is_active=True)
    ensure_boosters(deactivate_obsolete=True)
    assert Booster.objects.get(slug="emea-2025").is_active


def test_the_base_booster_only_has_teams_qualified_for_an_international(split):
    from cards.services import ensure_boosters
    from esport.models import GameAppearance, Split, Tournament

    ensure_boosters()
    base = Booster.objects.get(slug="base")
    assert base.card_pool().count() == 0  # personne n'est allé à l'international

    qualified = Card.objects.filter(split=split).first().team
    msi = make_league("Mid-Season Invitational", "MSI", "International")
    msi_split = Split.objects.get(league=msi)
    Split.objects.filter(pk=msi_split.pk).update(year=split.year)
    tournament = Tournament.objects.create(overview_page="MSI 2025", name="MSI 2025", league=msi,
                                           split=msi_split, year=split.year)
    game = GameAppearance.objects.filter(team=qualified).first()
    game.pk = None
    game.tournament = tournament
    game.game_id = "msi-game-1"
    game.save()

    pool = base.card_pool()
    assert pool.count() == Card.objects.filter(split=split, team=qualified).count() > 0
    assert set(pool.values_list("team", flat=True)) == {qualified.pk}


def test_init_boosters_retires_the_old_international_booster(split):
    from django.core.management import call_command

    make_league("Esports World Cup", "EWC", "International")
    old = Booster.objects.create(name="International 2026", slug="international-2026", auto=True)
    by_hand = Booster.objects.create(name="Le mien", slug="le-mien")

    call_command("init_boosters")

    old.refresh_from_db()
    by_hand.refresh_from_db()
    assert not old.is_active and by_hand.is_active
    assert Booster.objects.filter(slug="ewc").exists()
    assert Booster.objects.get(slug="base").is_active


def test_the_gestion_creates_the_booster_types(admin_client, split):
    url = reverse("site:cards_booster_boosters-types")
    assert admin_client.get(url).status_code == 200
    response = admin_client.post(url, {"deactivate_obsolete": True}, content_type="application/json")
    assert response.status_code == 200, response.content
    assert Booster.objects.filter(slug="emea-2025").exists()


def test_the_site_name_is_leaguegacha(client, split):
    page = client.get(reverse("cards:home")).content.decode()
    assert "League<b>Gacha</b>" in page and "Wiki LoL" not in page


def test_the_footer_shows_the_deployed_version(client, split, settings):
    settings.APP_VERSION = "v1.2.3"
    assert "v1.2.3" in client.get(reverse("cards:home")).content.decode()
    settings.APP_VERSION = ""
    assert 'class="version"' not in client.get(reverse("cards:home")).content.decode()


# --------------------------------------------------------------------- Prestige des ligues
def test_the_same_games_rate_lower_in_a_regional_league(split):
    from esport.importer import LeaguepediaImporter
    from esport.models import League, PlayerSplitStats

    lec = {s.player_id: s.score for s in PlayerSplitStats.objects.filter(split=split)}

    League.objects.filter(pk=split.league_id).update(short_name="LFL", region="EMEA", level="Secondary")
    split.league.refresh_from_db()
    LeaguepediaImporter.compute_split_stats(split)
    lfl = {s.player_id: s.score for s in PlayerSplitStats.objects.filter(split=split)}
    assert all(lfl[p] < lec[p] for p in lec)

    League.objects.filter(pk=split.league_id).update(prestige="1.10")
    split.league.refresh_from_db()
    LeaguepediaImporter.compute_split_stats(split)
    strong = {s.player_id: s.score for s in PlayerSplitStats.objects.filter(split=split)}
    assert all(strong[p] > lec[p] for p in lec if lec[p] > 0)


def test_the_pack_announces_its_best_rarity(client, booster, bob):
    client.force_login(bob)
    response = client.post(reverse("cards:open_booster", args=[booster.slug]))
    page = client.get(response["Location"])
    best = page.context["best"]
    html = page.content.decode()
    assert f'data-best="{best.rarity.order}"' in html
    assert f"--best: {best.rarity.color}" in html
    assert 'class="pack-aura"' in html


def test_default_drop_rates():
    from cards.models import Rarity
    from cards.services import ensure_default_rarities

    ensure_default_rarities()
    rates = {r.code: float(r.drop_rate) for r in Rarity.objects.all()}
    assert rates == {"commune": 74, "rare": 20, "epique": 5, "legendaire": 1}


def test_the_gestion_resets_chosen_players(admin_client, admin_user, booster, bob, full_team):
    from cards.models import BoosterStock, PackOpening
    from cards.services import give_boosters, open_booster

    alice = get_user_model().objects.create_user("alice", password="mdp-alice-123")
    open_booster(bob, booster)
    open_booster(alice, booster)
    save_roster(bob, "Ma team", full_team)
    give_boosters([bob], booster, 2, given_by=admin_user)

    url = reverse("site:cards_usercard_reinitialiser")
    assert admin_client.get(url).status_code == 200
    refused = admin_client.post(url, {"users": [bob.pk]}, content_type="application/json")
    assert refused.status_code == 400 and UserCard.objects.filter(user=bob).exists()

    response = admin_client.post(url, {"users": [bob.pk], "confirm": True}, content_type="application/json")
    assert response.status_code == 200, response.content
    assert "1 joueur(s) remis à zéro" in response.json()["_message"]
    assert not UserCard.objects.filter(user=bob).exists()
    assert not PackOpening.objects.filter(user=bob).exists()
    assert not Roster.objects.filter(user=bob).exists()
    assert not BoosterStock.objects.filter(user=bob).exists()
    assert UserCard.objects.filter(user=alice).exists()  # les autres ne bougent pas
    assert booster_status(bob, [booster])[booster.pk]["available"] == booster.starting_stock


def test_a_booster_without_visual_shows_its_players_in_legendary(client, booster, bob):
    from cards.services import format_rate

    client.force_login(bob)
    page = client.get(reverse("cards:home"))
    showcase = page.context["boosters"][0].showcase
    assert 1 <= len(showcase) <= 6
    assert all(item["rarity"].code == "legendaire" for item in showcase)
    assert showcase[0]["rate"] == "0,1 %"  # 1 % en légendaire, 10 joueurs
    html = page.content.decode()
    assert "data-showcase" in html and "showcase-rate" in html
    assert format_rate(1 / 480) == "0,0021 %" and format_rate(2.5) == "2,5 %"


# --------------------------------------------------------------------- Portails
@pytest.fixture
def portal_booster(split):
    return Booster.objects.create(name="EMEA 2025", years="2025", is_active=False, cards_per_pack=5)


def test_the_daily_portal_rotates_between_inactive_boosters(split, portal_booster):
    from cards.services import ensure_daily_portal

    other = Booster.objects.create(name="Autre", is_active=False)
    Booster.objects.create(name="Actif", is_active=True)
    now = timezone.now()
    first = ensure_daily_portal(now)
    assert first.auto and first.booster in {portal_booster, other}
    assert first.ends_at - first.starts_at == timedelta(hours=24)
    assert first.starts_at <= now < first.ends_at
    assert ensure_daily_portal(now) == first  # un seul par jour
    second = ensure_daily_portal(now + timedelta(days=1))
    assert second.booster != first.booster  # l'autre booster inactif


def test_a_portal_gives_two_boosters_twelve_hours_apart(split, portal_booster, bob):
    from cards.models import Portal
    from cards.services import open_portal, portal_status

    now = timezone.now()
    portal = Portal.objects.create(booster=portal_booster, starts_at=now - timedelta(hours=1),
                                   ends_at=now + timedelta(days=1))
    assert portal_status(bob, portal, now)["available"]

    opening = open_portal(bob, portal)
    assert opening.portal == portal and opening.cards.count() == 5
    status = portal_status(bob, portal)
    assert not status["available"] and status["next_at"] == opening.opened_at + timedelta(hours=12)
    with pytest.raises(GameError, match="pas encore prêt"):
        open_portal(bob, portal)

    later = opening.opened_at + timedelta(hours=12, minutes=1)
    open_portal(bob, portal, now=later)
    assert portal_status(bob, portal, later)["done"]
    with pytest.raises(GameError, match="tout ce que"):
        open_portal(bob, portal, now=later)
    with pytest.raises(GameError, match="fermé"):
        open_portal(bob, portal, now=portal.ends_at)


def test_a_second_booster_that_would_come_after_closing_is_not_announced(split, portal_booster, bob):
    from cards.models import PackOpening, Portal
    from cards.services import portal_status

    now = timezone.now()
    portal = Portal.objects.create(booster=portal_booster, starts_at=now - timedelta(hours=20),
                                   ends_at=now + timedelta(hours=4))
    PackOpening.objects.create(user=bob, booster=portal_booster, portal=portal)
    status = portal_status(bob, portal, now)
    assert status["done"] and not status["available"]


def test_a_manual_portal_lasts_one_to_thirty_days(portal_booster):
    from django.core.exceptions import ValidationError

    from cards.models import Portal

    now = timezone.now()
    Portal(booster=portal_booster, starts_at=now, ends_at=now + timedelta(days=30)).full_clean()
    for days in (0.5, 31):
        with pytest.raises(ValidationError):
            Portal(booster=portal_booster, starts_at=now, ends_at=now + timedelta(days=days)).full_clean()


def test_the_home_page_shows_the_portal_and_opens_it(client, portal_booster, bob):
    client.force_login(bob)
    page = client.get(reverse("cards:home"))
    portal = page.context["portals"][0]
    assert portal.booster == portal_booster and portal.status["available"]
    assert "le premier est offert" in page.content.decode()
    response = client.post(reverse("cards:open_portal", args=[portal.pk]))
    assert response.status_code == 302 and "/ouverture/" in response["Location"]
    html = client.get(reverse("cards:home")).content.decode()
    assert "suivant dans" in html or "après la fermeture" in html


def test_the_gestion_lists_portals(admin_client, portal_booster):
    assert admin_client.get(reverse("site:cards_portal_list")).status_code == 200


@override_settings(GAME_STAFF_UNLIMITED=False)
def test_another_booster_is_offered_only_when_one_is_available(client, booster, bob):
    client.force_login(bob)
    first = client.post(reverse("cards:open_booster", args=[booster.slug]))
    assert "Ouvrir un autre" in client.get(first["Location"]).content.decode()  # il en reste 1
    last = client.post(reverse("cards:open_booster", args=[booster.slug]))
    html = client.get(last["Location"]).content.decode()
    assert "Ouvrir un autre" not in html and "Prochain Test dans" in html


@override_settings(GAME_STAFF_UNLIMITED=False)
def test_after_a_portal_booster_the_next_one_comes_from_the_portal(client, portal_booster, bob):
    from cards.models import Portal

    now = timezone.now()
    portal = Portal.objects.create(booster=portal_booster, starts_at=now - timedelta(hours=1),
                                   ends_at=now + timedelta(days=2))
    client.force_login(bob)
    response = client.post(reverse("cards:open_portal", args=[portal.pk]))
    html = client.get(response["Location"]).content.decode()
    assert "Ouvrir un autre" not in html and "Prochain booster du portail dans" in html

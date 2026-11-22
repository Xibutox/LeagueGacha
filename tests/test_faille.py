"""La Faille : règles (sans Django), puis partie, boutique, sauvegardes et succès."""
import random
import time

import pytest
from django.core.cache import cache
from django.urls import reverse

from cards import faille
from cards import faille_rules as R
from cards.models import FailleAchievement, FailleData, FailleRun

from .test_game import bob, champion_ids, full_team, give, owned  # noqa: F401  (fixtures)


def ddragon_item(name, cost, recipe=None, stats="", tags=(), frm=None, into=None, purchasable=True):
    return {
        "name": name, "description": f"<mainText><stats>{stats}</stats></mainText>", "plaintext": name,
        "gold": {"base": recipe if recipe is not None else cost, "total": cost, "purchasable": purchasable},
        "maps": {"11": True}, "tags": list(tags), **({"from": frm} if frm else {}), **({"into": into} if into else {}),
    }


DDRAGON = {"data": {
    "1036": ddragon_item("Épée longue", 350, stats="<attention>+10</attention> dégâts d'attaque", tags=["Damage"], into=["3031"]),
    "1018": ddragon_item("Cape d'agilité", 600, stats="<attention>+15%</attention> de chances de coup critique",
                         tags=["CriticalStrike"], into=["3031"]),
    "3031": ddragon_item("Lame d'infini", 3400, recipe=2450, frm=["1036", "1018"], tags=["Damage", "CriticalStrike"],
                         stats="<attention>+75</attention> dégâts d'attaque<br><attention>+25%</attention> de chances de coup critique"
                               "<br><attention>+30%</attention> de dégâts de coup critique"),
    "1001": ddragon_item("Bottes", 300, stats="<attention>+25</attention> vitesse de déplacement", tags=["Boots"]),
    "3340": ddragon_item("Balise furtive", 0, tags=["Trinket"]),
    "2003": ddragon_item("Potion de soin", 50, tags=["Consumable"]),
    "2140": ddragon_item("Élixir de rage", 500, tags=["Consumable"]),
    "223031": ddragon_item("Lame d'infini (Arène)", 3400),
}}


# --------------------------------------------------------------------- Règles
def test_the_catalog_keeps_the_curated_faille_items_only():
    cat = R.curate(R.catalog_from_ddragon(DDRAGON))
    assert set(cat) == {"1036", "1018", "3031", "2140"}  # pas les bottes, ni l'objet d'Arène
    assert cat["3031"]["kind"] == "legendary" and cat["3031"]["cls"] == "Clic" and cat["3031"]["branch"] == "clic"
    assert cat["3031"]["stats"] == {"ad": 65, "crit": 25}
    assert cat["1036"]["kind"] == "component" and cat["1036"]["into"] == ["3031"]
    assert cat["2140"]["kind"] == "elixir" and cat["2140"]["stats"]["ad"] > 0


def test_a_missing_component_is_folded_into_the_recipe():
    data = {"data": dict(DDRAGON["data"], **{"3031": dict(DDRAGON["data"]["3031"], **{"from": ["1036", "9999"]}),
                                           "9999": ddragon_item("Inconnu", 400)})}
    cat = R.curate(R.catalog_from_ddragon(data))
    assert cat["3031"]["from"] == ["1036"] and cat["3031"]["recipe"] == 3400 - 350  # le prix total ne change pas


def test_the_enemy_sequence_is_the_same_for_everyone_in_a_week():
    seed = R.week_seed("2026-11-23")
    assert R.sequence(seed, 0, 50) == R.sequence(seed, 0, 50)
    assert R.sequence(R.week_seed("2026-11-30"), 0, 50) != R.sequence(seed, 0, 50)
    first = R.sequence(seed, 0, 5)
    assert all(e["kind"] in R.MINIONS for e in first)  # les camps arrivent plus tard
    assert R.enemy_at(seed, R.BARON_EVERY)["kind"] == "baron"
    assert R.enemy_at(seed, R.BARON_EVERY, elder=True)["kind"] == "elder"
    assert R.enemy_at(seed, R.DRAKE_EVERY)["kind"] in R.DRAKES
    # Les ennemis grandissent.
    assert R.enemy_at(seed, 150)["hp"] > R.enemy_at(seed, 15)["hp"] * 10 or R.enemy_at(seed, 150)["kind"] != R.enemy_at(seed, 15)["kind"]


def test_augments_offered_are_distinct_and_never_an_owned_forge():
    rng = random.Random(3)
    for _ in range(50):
        offer = R.roll_augments(rng, ["forge_clic"])
        assert len(offer) == 3 == len(set(offer))
        assert "forge_clic" not in offer


def test_stats_add_items_and_masterworks():
    cat = R.curate(R.catalog_from_ddragon(DDRAGON))
    base = R.compute_stats({}, cat)
    with_ie = R.compute_stats({"inv": [{"id": "3031"}]}, cat)
    ornn = R.compute_stats({"inv": [{"id": "3031", "ornn": True}]}, cat)
    assert with_ie["ad"] == base["ad"] + 65
    assert ornn["ad"] == base["ad"] + 65 * R.ORNN_MULT
    assert with_ie["critdmg"] == base["critdmg"] + 40
    assert base["passive_dps"] == 0
    assert R.max_dps(ornn) > R.max_dps(with_ie) > R.max_dps(base) > 0


def test_magic_items_deal_passive_damage():
    cat = {"3089": {"id": "3089", "stats": {"ap": 130}, "passive": R.PASSIVES["3089"]}}
    stats = R.compute_stats({"inv": [{"id": "3089"}]}, cat)
    assert stats["ap"] == pytest.approx(130 * 1.4)
    assert stats["passive_dps"] == pytest.approx(130 * 1.4 * R.AP_DPS)


# --------------------------------------------------------------------- Partie
@pytest.fixture
def catalog(db):
    FailleData.objects.create(key="items", version="16.0.1", data={"items": R.catalog_from_ddragon(DDRAGON)})
    cache.delete("faille:catalog")
    return faille.catalog()


def kills_until(run, last):
    """Une sauvegarde qui passe toutes les étapes jusqu'à last (incluse)."""
    return {"duration": 600, "clicks": 300, "kills": [[s, 1] for s in range(run.state["best"], last + 1)], "step": last + 1}


def test_a_run_starts_with_gold_and_buys_with_components(bob, catalog):  # noqa: F811
    run = faille.get_run(bob)
    assert run.state["gold"] == faille.START_GOLD
    faille.buy(run, "1036")
    assert run.state["gold"] == faille.START_GOLD - 350
    assert faille.purchase_cost("3031", run.state["inv"], catalog) == (2450 + 600, [0])
    run.state["gold"] = 5000
    faille.buy(run, "3031")
    assert [s["id"] for s in run.state["inv"]] == ["3031"]  # l'épée longue est consommée
    assert run.state["gold"] == 5000 - 3050
    with pytest.raises(faille.FailleError):
        faille.buy(run, "3031")  # un seul exemplaire d'un légendaire


def test_the_inventory_has_six_slots(bob, catalog):  # noqa: F811
    run = faille.get_run(bob)
    run.state["gold"] = 10_000
    for _ in range(6):
        faille.buy(run, "1036")
    with pytest.raises(faille.FailleError, match="Inventaire plein"):
        faille.buy(run, "1018")
    refund = faille.sell(run, 0)["refund"]
    assert refund == round(350 * faille.SELL_RATE) and len(run.state["inv"]) == 5


def test_a_sync_progresses_pays_gold_and_rejects_cheats(bob, catalog):  # noqa: F811
    run = faille.get_run(bob)
    run.state["synced"] = time.time() - 600
    result = faille.apply_sync(run, kills_until(run, 9))
    assert result["ok"]
    run.refresh_from_db()
    assert run.best == 10 and run.kills == 10 and run.state["gold"] > faille.START_GOLD
    # Sauter une étape : refusé.
    run.state["synced"] = time.time() - 60
    assert not faille.apply_sync(run, {"duration": 60, "kills": [[15, 1]], "clicks": 10})["ok"]
    # Des dégâts impossibles avec le build de départ : refusé.
    run.state["synced"] = time.time() - 120
    cheat = kills_until(run, 150)
    cheat["duration"] = 120
    assert not faille.apply_sync(run, cheat)["ok"]
    # L'or annoncé est plafonné par ce que rapportent les ennemis.
    run.state["synced"] = time.time() - 60
    before = run.state["gold"]
    faille.apply_sync(run, {"duration": 60, "kills": [[5, 1]], "clicks": 10, "gold": 1e9})
    assert run.state["gold"] - before < 50


def test_the_baron_offers_an_augment_and_a_forge_unlocks_ornn(bob, catalog):  # noqa: F811
    run = faille.get_run(bob)
    run.state.update(best=R.BARON_EVERY, step=R.BARON_EVERY, synced=time.time() - 600)
    run.state["inv"] = [{"id": "3031", "ornn": False}]
    run.state["augments"] = ["glass_cannon", "stats3"]  # assez de dégâts pour la vérification
    run.save()
    assert faille.apply_sync(run, {"duration": 600, "clicks": 100, "kills": [[R.BARON_EVERY, 1]]})["ok"]
    assert len(run.state["pending"]) == 1 and len(run.state["pending"][0]) == 3
    assert FailleAchievement.objects.filter(user=bob, code="baron").exists()
    # Repasser l'étape ne redonne pas d'augment.
    run.state["synced"] = time.time() - 600
    faille.apply_sync(run, {"duration": 600, "clicks": 100, "kills": [[R.BARON_EVERY, 1]]})
    assert len(run.state["pending"]) == 1
    code = run.state["pending"][0][0]
    faille.choose_augment(run, code)
    assert run.state["pending"] == []
    with pytest.raises(faille.FailleError, match="Forge d'Ornn"):
        faille.ornn(run, 0)
    run.state["augments"].append("forge_clic")
    run.state["gold"] = 5000
    faille.ornn(run, 0)
    assert run.state["inv"][0]["ornn"]
    assert FailleAchievement.objects.filter(user=bob, code="ornn").exists()


def test_offline_time_brings_gold_but_no_kills(bob, catalog):  # noqa: F811
    run = faille.get_run(bob)
    run.state["synced"] = time.time() - 3 * 3600
    gold = faille.credit_offline(run)
    assert gold > 0 and run.kills == 0
    run.state["synced"] = time.time() - 30 * 3600
    assert faille.credit_offline(run) <= R.BASE["goldgen"] / 10 * faille.OFFLINE_CAP * faille.OFFLINE_RATE + 1


def test_achievements_add_a_tiny_team_bonus(bob, full_team, catalog):  # noqa: F811
    from cards.services import save_roster

    roster = save_roster(bob, "Ma team", full_team)
    base = roster.score
    run = faille.get_run(bob)
    run.state["synced"] = time.time() - 600
    with pytest.MonkeyPatch.context():
        faille.apply_sync(run, kills_until(run, 3))
    assert FailleAchievement.objects.filter(user=bob, code="weeks_1").exists()
    from cards.services import refresh_roster

    refresh_roster(roster)
    roster.refresh_from_db()
    assert roster.details["faille"] == {"achievements": 1, "bonus": R.ACHIEVEMENT_BONUS}
    assert roster.score == pytest.approx(base * (1 + R.ACHIEVEMENT_BONUS / 100), abs=0.01)


def test_the_page_and_the_api(client, bob, catalog):  # noqa: F811
    client.force_login(bob)
    page = client.get(reverse("cards:faille"))
    assert page.status_code == 200 and "faille-data" in page.content.decode()
    answer = client.post(reverse("cards:faille_buy"), {"item": "1036"}, content_type="application/json").json()
    assert answer["state"]["inv"][0]["id"] == "1036"
    refused = client.post(reverse("cards:faille_buy"), {"item": "3031"}, content_type="application/json")
    assert refused.status_code == 400 and "PO" in refused.json()["error"]
    assert client.get(reverse("cards:faille_enemies") + "?from=0").json()["enemies"][0]["step"] == 0
    run = FailleRun.objects.get(user=bob)
    run.state["synced"] = time.time() - 60
    run.save()
    sync = client.post(reverse("cards:faille_sync"), {"duration": 30, "clicks": 20, "kills": [[0, 1]], "step": 1},
                       content_type="application/json")
    assert sync.status_code == 200 and sync.json()["state"]["best"] == 1
    ranking = client.get(reverse("cards:ranking") + "?faille=1")
    assert ranking.status_code == 200 and "bob" in ranking.content.decode()


def test_the_page_says_when_the_game_is_not_imported(client, bob):  # noqa: F811
    client.force_login(bob)
    assert "pas encore prêt" in client.get(reverse("cards:faille")).content.decode()


def test_an_old_run_is_migrated_with_a_refund(bob, catalog):  # noqa: F811
    run = faille.get_run(bob)
    run.state.update(v=1, gold=0, inv=[{"id": "1001", "ornn": False}, {"id": "1036", "ornn": False}],
                     augments=["forge_tireur", "forge_assassin", "forge_mage"])
    run.save()
    run = faille.get_run(bob)
    assert [s["id"] for s in run.state["inv"]] == ["1036"]
    assert run.state["gold"] == 300 and run.state["refund"] == 300  # les bottes remboursées
    assert run.state["augments"] == ["forge_clic", "forge_passif"]


def test_support_gold_generation_pays_while_playing(bob, catalog):  # noqa: F811
    run = faille.get_run(bob)
    gold = run.state["gold"]
    run.state["synced"] = time.time() - 100
    assert faille.apply_sync(run, {"duration": 100})["ok"]
    assert run.state["gold"] == pytest.approx(gold + R.BASE["goldgen"] / 10 * 100, rel=0.01)

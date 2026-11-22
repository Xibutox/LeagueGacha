"""Tests de la logique sans Django : client Leaguepedia, agrégation, note, rareté, tirage.

Lancer depuis la racine du projet :  python -m unittest tests.test_pure
"""
import random
import unittest
from collections import Counter

from cards import draw
from esport import scoring
from esport.leaguepedia import LeaguepediaClient, LeaguepediaError, in_clause, quote


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.text = str(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.calls_headers = []
        self.headers = {}

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(params)
        self.calls_headers.append(headers or {})
        return self.responses.pop(0)


def cargo_page(n, start=0):
    return {"cargoquery": [{"title": {"Name": f"P{start + i}", "Team": "G2 &amp; Co"}} for i in range(n)]}


class ClientTests(unittest.TestCase):
    def make(self, responses):
        session = FakeSession(responses)
        return LeaguepediaClient(session=session, delay=0, sleep=lambda s: None), session

    def test_pagination(self):
        client, session = self.make([FakeResponse(cargo_page(500)), FakeResponse(cargo_page(120, 500))])
        rows = client.cargo(tables="Players", fields="Players.ID=Name")
        self.assertEqual(len(rows), 620)
        self.assertEqual(session.calls[1]["offset"], 500)
        self.assertEqual(rows[0]["Team"], "G2 & Co")  # HTML décodé

    def test_ratelimit_retry(self):
        client, session = self.make([
            FakeResponse({"error": {"code": "ratelimited", "info": "slow down"}}),
            FakeResponse(cargo_page(3)),
        ])
        self.assertEqual(len(client.cargo(tables="T", fields="T.A=A")), 3)
        self.assertEqual(len(session.calls), 2)

    def test_other_error_raises(self):
        client, _ = self.make([FakeResponse({"error": {"code": "badfield", "info": "nope"}})])
        with self.assertRaises(LeaguepediaError):
            client.cargo(tables="T", fields="T.A=A")

    def test_image_urls_with_normalization(self):
        data = {
            "query": {
                "normalized": [{"from": "File:caps_2025.png", "to": "File:Caps 2025.png"}],
                "pages": {
                    "1": {"title": "File:Caps 2025.png", "imageinfo": [{"url": "u", "thumburl": "t"}]},
                    "-1": {"title": "File:Missing.png", "missing": ""},
                },
            }
        }
        client, session = self.make([FakeResponse(data)])
        urls = client.image_urls(["caps_2025.png", "Missing.png"], width=400)
        self.assertEqual(urls, {"caps_2025.png": "t"})
        self.assertEqual(session.calls[0]["iiurlwidth"], 400)

    def test_image_download_falls_back_to_another_header_set(self):
        png = {"Content-Type": "image/png"}
        forbidden = FakeResponse({}, 403)
        forbidden.headers = {"Content-Type": "text/html"}
        ok = FakeResponse({}, 200)
        ok.headers = png
        ok.content = b"PNG"
        images = FakeSession([forbidden, forbidden, ok, ok])
        client = LeaguepediaClient(session=FakeSession([]), image_session=images, delay=0, sleep=lambda s: None)

        self.assertEqual(client.download("thumb", "orig"), b"PNG")
        # 1er jeu : miniature et originale refusées ; 2e jeu : miniature OK.
        self.assertEqual(client.image_strategy, 1)
        self.assertIn("compatible", images.calls_headers[2]["User-Agent"])
        # La suite commence directement par le jeu qui a marché.
        client.download("thumb2")
        self.assertIn("compatible", images.calls_headers[3]["User-Agent"])

    def test_image_download_reports_every_attempt(self):
        refused = []
        for _ in range(6):
            r = FakeResponse({}, 403)
            r.headers = {"Content-Type": "text/html"}
            refused.append(r)
        client = LeaguepediaClient(
            session=FakeSession([]), image_session=FakeSession(refused), delay=0, sleep=lambda s: None
        )
        from esport.leaguepedia import ImageDownloadError

        with self.assertRaises(ImageDownloadError) as caught:
            client.download("thumb", "orig")
        self.assertEqual(str(caught.exception).count("HTTP 403"), 6)

    def test_quote(self):
        self.assertEqual(quote('Rekkles "the" GOAT'), '"Rekkles \\"the\\" GOAT"')
        self.assertEqual(in_clause("P.ID", []), "1=0")
        self.assertEqual(in_clause("P.ID", ["a", "b"]), 'P.ID IN ("a", "b")')


def row(**kw):
    base = dict(kills=2, deaths=2, assists=4, gold=10000, cs=250, damage=15000, vision_score=30,
                team_kills=15, team_gold=50000, team_damage=60000, game_length=30.0, win=True,
                role="Mid", team=1, champion="Ahri")
    base.update(kw)
    return base


class ScoringTests(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(scoring.parse_int("1,234"), 1234)
        self.assertIsNone(scoring.parse_date("0000-00-00"))
        self.assertEqual(str(scoring.parse_date("1996-05-07")), "1996-05-07")
        self.assertTrue(scoring.parse_bool("Yes"))
        self.assertEqual(scoring.normalize_role("ADC"), "Bot")
        self.assertIsNotNone(scoring.parse_datetime_utc("2025-01-18 17:03:00"))

    def test_aggregate(self):
        agg = scoring.aggregate_appearances([row(), row(win=False, deaths=0, champion="Azir")])
        self.assertEqual(agg["games"], 2)
        self.assertEqual(agg["wins"], 1)
        self.assertEqual(agg["kda"], round((4 + 8) / 2, 2))
        self.assertEqual(agg["kill_participation"], 40.0)
        self.assertEqual(agg["cs_per_min"], round(500 / 60, 2))
        self.assertEqual(agg["damage_share"], 25.0)
        self.assertEqual(agg["role"], "Mid")
        self.assertEqual(len(agg["champions"]), 2)

    def test_missing_game_length_and_vision(self):
        agg = scoring.aggregate_appearances([row(game_length=None, vision_score=None)])
        self.assertIsNone(agg["cs_per_min"])  # inconnu, pas zéro
        self.assertIsNone(agg["vision_per_min"])

    def test_missing_damage_is_not_a_zero(self):
        """Les anciennes saisons n'ont pas de dégâts sur Leaguepedia : ni DPM
        à zéro, ni note plombée."""
        old = row(damage=None, team_damage=None, vision_score=None)
        agg = scoring.aggregate_appearances([old, row()])
        self.assertEqual(agg["damage_per_min"], round(row()["damage"] / row()["game_length"], 1))
        self.assertIsNone(scoring.aggregate_appearances([old])["damage_per_min"])
        self.assertIsNone(scoring.aggregate_appearances([old])["damage_share"])
        metrics = scoring.game_metrics(old)
        self.assertIsNone(metrics["dpm"])
        self.assertIsNone(metrics["dmg_share"])
        self.assertIsNone(metrics["dmg_per_gold"])

        games = [row(kills=k, deaths=2, damage=10000 + 1000 * k) for k in range(5)]
        ref = scoring.reference(games)
        with_damage, _ = scoring.game_rating(games[2], ref)
        without, zs = scoring.game_rating(dict(games[2], damage=None, team_damage=None), ref)
        self.assertNotIn("dpm", zs)
        self.assertAlmostEqual(with_damage, without, delta=12)  # pas d'effondrement

    def test_the_reference_falls_back_when_a_metric_is_rare(self):
        modern = [row(kills=k, damage=20000 + 1000 * k) for k in range(5)]
        old = [row(kills=k, damage=None, team_damage=None) for k in range(5)]
        overall = scoring.reference(modern + old)
        year = scoring.reference(old, fallback=overall, min_samples=3)
        self.assertEqual(year["Mid"]["dpm"], overall["Mid"]["dpm"])  # pas de dégâts cette année-là

    def test_an_average_game_scores_50(self):
        games = [row(kills=k, deaths=2, damage=10000 + 1000 * k) for k in range(5)]
        ref = scoring.reference(games)
        mid_game, _ = scoring.game_rating(games[2], ref)
        best, _ = scoring.game_rating(games[4], ref)
        worst, _ = scoring.game_rating(games[0], ref)
        self.assertAlmostEqual(mid_game, 50, delta=8)
        self.assertGreater(best, mid_game)
        self.assertLess(worst, mid_game)

    def test_a_support_is_judged_against_supports(self):
        mids = [row(damage=20000 + 1000 * i) for i in range(5)]
        sups = [row(role="Support", kills=0, damage=4000 + 500 * i, vision_score=60 + 10 * i) for i in range(5)]
        ref = scoring.reference(mids + sups)
        best_support, _ = scoring.game_rating(sups[4], ref)
        # Peu de dégâts dans l'absolu, mais beaucoup pour un support : bonne note.
        self.assertGreater(best_support, 55)
        as_mid, _ = scoring.game_rating(dict(sups[4], role="Mid"), ref)
        self.assertLess(as_mid, best_support)

    def test_one_great_game_weighs_less_than_many(self):
        self.assertLess(scoring.split_rating([90]), scoring.split_rating([90] * 20))
        self.assertEqual(scoring.split_rating([]), scoring.LEAGUE_BASE)

    def test_missing_data_is_ignored(self):
        ref = scoring.reference([row(), row(kills=5)])
        score, zs = scoring.game_rating(row(game_length=None, vision_score=None), ref)
        self.assertNotIn("dpm", zs)
        self.assertTrue(0 <= score <= 100)

    def test_dimensions_and_percentiles(self):
        dims = scoring.dimension_scores([{"kda": 1.0, "deaths_pm": 1.0, "win": -1.0}])
        self.assertGreater(dims["combat"], 50)
        self.assertLess(dims["victoire"], 50)
        stats = scoring.rank_percentiles([{"rating": 40}, {"rating": 60}, {"rating": 80}])
        self.assertEqual([s["percentile"] for s in stats], [0, 50, 100])

    def test_overall(self):
        self.assertEqual(scoring.overall_rating(50), 74)
        self.assertEqual(scoring.overall_rating(0), 50)
        self.assertEqual(scoring.overall_rating(100), 99)


RULES = [
    draw.RarityRule("C", 0, 62),
    draw.RarityRule("R", 1, 27),
    draw.RarityRule("E", 2, 9),
    draw.RarityRule("L", 3, 2),
]


class DrawTests(unittest.TestCase):
    def test_pack_size_and_no_duplicates(self):
        pack = draw.draw_pack(list(range(20)), RULES, 5, rng=random.Random(1))
        self.assertEqual(len(pack), 5)
        self.assertEqual(len({c for _, c in pack}), 5)

    def test_every_player_can_come_at_every_rarity(self):
        rng = random.Random(3)
        seen = {(r, c) for _ in range(3000) for r, c in draw.draw_pack([1, 2], RULES, 2, rng=rng)}
        self.assertEqual({c for r, c in seen if r == "L"}, {1, 2})

    def test_guaranteed_last_slot(self):
        for seed in range(200):
            pack = draw.draw_pack(list(range(50)), RULES, 5, guaranteed_order=1, rng=random.Random(seed))
            self.assertIn(pack[-1][0], {"R", "E", "L"})

    def test_small_pool_and_empty_pool(self):
        pack = draw.draw_pack([1, 2], RULES, 4, rng=random.Random(0))
        self.assertEqual(len(pack), 4)
        self.assertEqual(draw.draw_pack([], RULES, 3), [])

    def test_distribution_roughly_matches_weights(self):
        rng = random.Random(42)
        counts = Counter(r for _ in range(4000) for r, _ in draw.draw_pack(list(range(100)), RULES, 5, rng=rng))
        total = sum(counts.values())
        self.assertAlmostEqual(counts["C"] / total, 0.62, delta=0.02)
        self.assertAlmostEqual(counts["L"] / total, 0.02, delta=0.006)

    def test_best_image(self):
        candidates = [
            {"key": "old", "tournament": "LEC 2023 Summer", "team": "G2 Esports"},
            {"key": "exact", "tournament": "LEC 2025 Spring", "team": "G2 Esports"},
            {"key": "future", "tournament": "LEC 2026 Winter", "team": "G2 Esports"},
        ]
        self.assertEqual(draw.best_image(candidates, "LEC 2025 Spring", "LEC", 2025, "G2 Esports"), "exact")
        # Sans photo du split : on préfère une photo passée plutôt qu'une photo future.
        self.assertEqual(draw.best_image(candidates[::2], "LEC 2025 Spring", "LEC", 2025), "old")
        self.assertIsNone(draw.best_image([], "x", "LEC", 2025))


class TeamScoringTests(unittest.TestCase):
    def test_history_lifts_a_short_current_sample(self):
        # 2 parties moyennes cette saison, 5 très bonnes avant.
        now_only = scoring.champion_rating([50, 50], 55)
        with_history = scoring.champion_rating([50, 50], 55, history_scores=[80] * 5)
        self.assertGreater(with_history, now_only + 4)
        # L'historique compte moins que la forme du moment.
        current = scoring.champion_rating([80] * 5, 55)
        self.assertGreater(current, with_history)

    def test_rarity_bonus_and_unplayed(self):
        self.assertEqual(scoring.slot_score(60, 80), 70)
        self.assertEqual(scoring.slot_score(60, 80, 10), 77)
        self.assertEqual(scoring.unplayed_rating(60), 50)

    def test_a_balanced_composition_is_coherent(self):
        def champ(tags, attack, defense, magic):
            return {"tags": tags, "attack": attack, "defense": defense, "magic": magic}

        balanced = [
            champ(["Tank", "Fighter"], 5, 9, 3),      # Top
            champ(["Fighter", "Assassin"], 7, 5, 2),  # Jungle
            champ(["Mage"], 2, 3, 9),                 # Mid
            champ(["Marksman"], 9, 2, 1),             # Bot
            champ(["Support", "Mage"], 2, 4, 8),      # Support
        ]
        all_ad = [champ(["Marksman"], 9, 2, 1)] * 3 + [champ(["Assassin"], 9, 2, 1)] * 2
        good = scoring.composition_coherence(balanced)
        bad = scoring.composition_coherence(all_ad)
        self.assertGreaterEqual(good["score"], 90)
        carries = {c["label"]: c["detail"] for c in good["checks"]}["Sources de dégâts"]
        self.assertEqual(carries, "2 carrys")  # le support mage ne compte pas
        self.assertLess(bad["score"], 40)
        labels = {c["label"]: c["state"] for c in bad["checks"]}
        self.assertEqual(labels["Ligne de front"], "bad")
        self.assertNotEqual(labels["Dégâts variés"], "ok")

    def test_duos_seen_in_pro_play_add_a_bonus(self):
        comp = [{"tags": ["Mage"], "attack": 2, "defense": 3, "magic": 9}] * 5
        self.assertGreater(scoring.composition_coherence(comp, duos_seen=2)["score"],
                           scoring.composition_coherence(comp)["score"])

    def test_unknown_champions_count_as_neutral(self):
        self.assertEqual(scoring.composition_coherence([{}] * 5)["score"], 60)

    def test_composition_score_mixes_slots_and_coherence(self):
        self.assertEqual(scoring.composition_score([80] * 5, 40), 70)


class PrestigeTests(unittest.TestCase):
    def test_defaults_follow_the_strength_of_leagues(self):
        from esport.prestige import default_prestige

        self.assertGreater(default_prestige("LCK"), default_prestige("LEC"))
        self.assertGreater(default_prestige("LEC"), default_prestige("LCS"))
        self.assertGreater(default_prestige("LCS"), default_prestige("LFL"))
        self.assertGreater(default_prestige("LCK"), default_prestige("LPL"))
        self.assertLessEqual(default_prestige("LFL"), 0.80)
        self.assertEqual(default_prestige("XYZ", "Korea", "Secondary"), 0.80)

    def test_international_competitions_are_not_corrected(self):
        from esport.prestige import default_prestige

        for short in ("MSI", "WCS", "FST", "EWC"):
            self.assertEqual(default_prestige(short, "International"), 1.0)
        self.assertEqual(default_prestige("XYZ", "International"), 1.0)

    def test_a_league_can_override_its_prestige(self):
        from decimal import Decimal
        from types import SimpleNamespace

        from esport.prestige import league_prestige

        league = SimpleNamespace(short_name="LFL", region="EMEA", level="Secondary", prestige=None)
        self.assertEqual(league_prestige(league), 0.80)
        league.prestige = Decimal("0.75")
        self.assertEqual(league_prestige(league), 0.75)

    def test_league_averages_are_spaced_like_dpm(self):
        from esport.prestige import default_prestige, league_base

        def average_card(short):
            return scoring.overall_rating(scoring.split_rating([], prior_mean=league_base(default_prestige(short))))

        # Le joueur moyen de chaque ligue (dpm.lol 2026 + 15 : 80, 77, 72, 68).
        self.assertEqual(average_card("LCK"), 80)
        self.assertAlmostEqual(average_card("LPL"), 77, delta=1)
        self.assertAlmostEqual(average_card("LEC"), 72, delta=1)
        self.assertAlmostEqual(average_card("LCS"), 68, delta=1)

    def test_the_same_games_rate_higher_in_a_stronger_league(self):
        from esport.prestige import league_base

        def rate(prestige):
            base = league_base(prestige)
            return scoring.split_rating([scoring.game_value(65, True, base)] * 20, prior_mean=base)

        self.assertGreater(rate(1.0), rate(0.92) + 10)

    def test_winning_and_playing_more_matter(self):
        base = 46.0  # LEC
        good_stats_losing = [scoring.game_value(65, i < 5, base) for i in range(11)]
        good_stats_winning = [scoring.game_value(65, i < 8, base) for i in range(11)]
        self.assertGreater(scoring.split_rating(good_stats_winning, prior_mean=base),
                           scoring.split_rating(good_stats_losing, prior_mean=base) + 6)
        # Peu de parties : la note reste plus près de la moyenne de la ligue.
        few = scoring.split_rating([scoring.game_value(70, True, base)] * 5, prior_mean=base)
        many = scoring.split_rating([scoring.game_value(70, True, base)] * 40, prior_mean=base)
        self.assertLess(few, many)
        self.assertGreater(few, base)

    def test_the_split_prior_is_the_league_average(self):
        self.assertLess(scoring.split_rating([], prior_mean=46), scoring.split_rating([], prior_mean=50))


if __name__ == "__main__":
    unittest.main()


class CatalogTests(unittest.TestCase):
    def test_old_and_renamed_major_leagues_are_known(self):
        from esport.catalog import FULL_IMPORT, MAJOR_LEAGUES
        from esport.prestige import default_prestige

        self.assertIn("LoL The Champions", MAJOR_LEAGUES["LCK"])  # Corée 2012-2015
        self.assertIn("League of Legends Championship of The Americas North", MAJOR_LEAGUES["LCS"])  # 2025
        self.assertIn("LTA N", FULL_IMPORT)
        self.assertEqual(default_prestige("LTC", "Korea", "Primary"), default_prestige("LCK"))

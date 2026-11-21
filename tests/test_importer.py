"""L'import Leaguepedia de bout en bout, avec un faux client qui répond ce
que l'API Cargo répondrait (les champs aliasés, en chaînes)."""

import io

import pytest
from PIL import Image

from cards.models import Card
from cards.services import download_card_images, ensure_default_rarities, generate_cards
from esport.importer import LeaguepediaImporter
from esport.models import GameAppearance, Player, PlayerSplitStats, Split, Tournament

pytestmark = pytest.mark.django_db

ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"]
TEAMS = {"G2 Esports": "G2", "Fnatic": "FNC"}


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "gold").save(buffer, "PNG")
    return buffer.getvalue()


def scoreboard():
    rows = []
    for game in range(3):
        for t, team in enumerate(TEAMS):
            for r, role in enumerate(ROLES):
                name = f"{TEAMS[team]}{role}"
                rows.append({
                    "GameId": f"LEC/2025/W1_{game}", "Link": name, "Name": name,
                    "Team": team, "TeamVs": list(TEAMS)[1 - t], "Role": role,
                    "Champion": "Azir", "Kills": str(r + t), "Deaths": "2",
                    "Assists": "4", "Gold": "11000", "CS": "250",
                    "Damage": str(12000 + 500 * r), "VisionScore": "40",
                    "TeamKills": "15", "TeamGold": "60000",
                    "PlayerWin": "Yes" if (game + t) % 2 == 0 else "No",
                    "Side": "1", "DateTimeUTC": f"2025-01-1{game} 17:00:00",
                    "GameLength": "31.5",
                })
    return rows


class FakeClient:
    def __init__(self):
        self.calls = []

    def cargo(self, tables, fields, where=None, **kw):
        self.calls.append(tables)
        if tables.startswith("Leagues"):
            return [
                {"League": "LoL Champions Korea", "Short": "LCK", "Region": "Korea", "Level": "Primary", "IsOfficial": "Yes"},
                {"League": "LoL EMEA Championship", "Short": "LEC", "Region": "EMEA", "Level": "Primary", "IsOfficial": "Yes"},
            ]
        if tables.startswith("Tournaments"):
            return [
                {"Name": "LEC 2025 Winter", "OverviewPage": "LEC/2025 Season/Winter Season",
                 "DateStart": "2025-01-18", "DateEnd": "2025-02-10", "League": "LoL EMEA Championship",
                 "LeagueShort": "LEC", "Region": "EMEA", "Year": "2025", "Split": "Winter",
                 "Level": "Primary", "IsPlayoffs": "0", "IsQualifier": "0"},
                {"Name": "LEC 2025 Winter Playoffs", "OverviewPage": "LEC/2025 Season/Winter Playoffs",
                 "DateStart": "2025-02-15", "DateEnd": "2025-03-02", "League": "LoL EMEA Championship",
                 "LeagueShort": "LEC", "Region": "EMEA", "Year": "2025", "Split": "Winter",
                 "Level": "Primary", "IsPlayoffs": "1", "IsQualifier": "0"},
            ]
        if tables.startswith("ScoreboardPlayers"):
            return scoreboard() if "Winter Season" in where else []
        if tables.startswith("PlayerRedirects"):
            # Un joueur introuvable : il doit finir « à vérifier ».
            return [
                {"AllName": f"{TEAMS[t]}{r}", "OverviewPage": f"{TEAMS[t]}{r}",
                 "IGN": f"{TEAMS[t]}{r}", "Image": f"{TEAMS[t]}{r}.png",
                 "Name": "Prénom Nom", "NativeName": "", "Country": "France",
                 "Nationality": "France", "Birthdate": "2001-05-07", "Role": r,
                 "Team": t, "IsRetired": "0"}
                for t in TEAMS for r in ROLES if f"{TEAMS[t]}{r}" != "FNCSupport"
            ]
        if tables.startswith("Players"):
            return []
        if tables.startswith("PlayerImages"):
            return [
                {"FileName": f"{TEAMS[t]}{r} 2025 Winter.png", "Link": f"{TEAMS[t]}{r}",
                 "Team": t, "Tournament": "LEC 2025 Winter", "ImageType": "", "Caption": ""}
                for t in TEAMS for r in ROLES
            ]
        if tables.startswith("Teams"):
            return [{"Name": t, "Short": s, "Image": f"{s}logo.png"} for t, s in TEAMS.items()]
        raise AssertionError(tables)

    image_strategy = None

    def image_infos(self, filenames, width=None):
        return {
            name: {"thumb": f"https://example.invalid/t/{name}", "url": f"https://example.invalid/{name}"}
            for name in filenames
        }

    def download(self, url, fallback_url=None):
        return png()


class BlockedCDN(FakeClient):
    """Le CDN de Fandom qui refuse toutes les images."""

    def __init__(self):
        super().__init__()
        self.downloads = 0

    def download(self, url, fallback_url=None):
        from esport.leaguepedia import ImageDownloadError

        self.downloads += 1
        raise ImageDownloadError("agent du projet (miniature) : HTTP 403")


def test_a_split_is_imported_from_tournaments_to_cards():
    ensure_default_rarities()
    importer = LeaguepediaImporter(client=FakeClient(), log=lambda m: None)

    rows = importer.discover_tournaments("LEC", 2025)
    importer.save_tournaments(rows)

    split = Split.objects.get()
    assert split.name == "LEC 2025"  # une ligue locale : toute l'année ensemble
    assert Tournament.objects.filter(split=split).count() == 2

    result = importer.import_split(split)
    created, _ = generate_cards(split)
    downloaded = download_card_images(split, importer)

    assert result["rows"] == 30
    assert GameAppearance.objects.count() == 30
    assert PlayerSplitStats.objects.filter(split=split).count() == 10
    assert created == 10
    assert Player.objects.get(overview_page="FNCSupport").needs_review
    caps = Player.objects.get(overview_page="G2Mid")
    assert caps.birthdate.year == 2001 and caps.country == "France"
    card = Card.objects.select_related("image").get(player=caps)
    assert card.image.tournament_name == "LEC 2025 Winter"
    assert card.image.file and card.image_url.startswith("/game-media/")
    assert downloaded >= 10
    damage = GameAppearance.objects.filter(player=caps).first()
    assert damage.team_damage == sum(12000 + 500 * r for r in range(5))


def test_importing_twice_replaces_rather_than_duplicates():
    ensure_default_rarities()
    importer = LeaguepediaImporter(client=FakeClient(), log=lambda m: None)
    importer.save_tournaments(importer.discover_tournaments("LEC", 2025))
    split = Split.objects.get()

    importer.import_split(split, with_images=False)
    importer.import_split(split, with_images=False)

    assert GameAppearance.objects.count() == 30


def test_a_blocked_cdn_stops_after_a_few_refusals_and_cards_still_exist():
    ensure_default_rarities()
    client = BlockedCDN()
    lines = []
    importer = LeaguepediaImporter(client=client, log=lines.append)
    importer.save_tournaments(importer.discover_tournaments("LEC", 2025))
    split = Split.objects.get()

    importer.import_split(split)
    generate_cards(split)
    download_card_images(split, importer)

    assert Card.objects.count() == 10
    assert importer.images_blocked
    assert client.downloads == LeaguepediaImporter.MAX_IMAGE_FAILURES
    assert any("refuse toutes les images" in line for line in lines)

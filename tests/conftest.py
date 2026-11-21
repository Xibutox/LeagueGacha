"""Des données nommées, partagées par les tests : un split de LEC avec dix
joueurs et six parties chacun, ses stats et ses cartes."""

import pytest

from cards.services import ensure_default_rarities, generate_cards
from esport.importer import LeaguepediaImporter
from esport.models import (
    GameAppearance,
    League,
    Player,
    PlayerImage,
    Split,
    Team,
    Tournament,
)

ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"]
CHAMPIONS = ["Ahri", "Azir", "Kai'Sa", "Wukong", "Lee Sin", "Nautilus", "Gnar"]


def build_split(n_players=10, games=6):
    league = League.objects.create(
        name="LoL EMEA Championship", short_name="LEC", region="EMEA"
    )
    split = Split.objects.create(
        league=league, name="LEC 2025 Spring", year=2025
    )
    tournament = Tournament.objects.create(
        overview_page="LEC/2025 Season/Spring Season",
        name="LEC 2025 Spring",
        league=league,
        split=split,
        year=2025,
    )
    teams = [
        Team.objects.create(name="G2 Esports", short_name="G2"),
        Team.objects.create(name="Fnatic", short_name="FNC"),
    ]
    for i in range(n_players):
        team = teams[i // 5 % 2]
        player = Player.objects.create(
            overview_page=f"Player{i}",
            ign=f"Player{i}",
            real_name=f"Joueur Numéro {i}",
            country="France",
            role=ROLES[i % 5],
        )
        PlayerImage.objects.create(
            player=player,
            filename=f"Player{i} LEC 2025 Spring.png",
            tournament_name="LEC 2025 Spring",
            team_name=team.name,
        )
        for g in range(games):
            GameAppearance.objects.create(
                game_id=f"G{g}",
                tournament=tournament,
                player=player,
                team=team,
                opponent=teams[1 - teams.index(team)].name,
                role=ROLES[i % 5],
                champion=CHAMPIONS[(i + g) % len(CHAMPIONS)],
                win=(g + i // 5) % 2 == 0,
                kills=i,
                deaths=2,
                assists=5,
                gold=10000,
                cs=200 + i,
                damage=10000 + 1000 * i,
                vision_score=30,
                team_kills=15,
                team_gold=50000,
                team_damage=60000,
                game_length=30.0,
            )
    return split


@pytest.fixture
def split(db):
    ensure_default_rarities()
    split = build_split()
    LeaguepediaImporter.compute_split_stats(split)
    generate_cards(split)
    return split

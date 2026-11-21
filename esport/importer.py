"""Import des données Leaguepedia vers la base locale.

Étapes pour un split :
    1. import_tournament_games : lignes de scoreboard (ScoreboardPlayers + durée de partie)
    2. sync_players            : infos joueurs (nom, naissance, pays…) via PlayerRedirects/Players
    3. sync_player_images      : liste des photos disponibles (PlayerImages)
    4. compute_split_stats     : agrégats + note de performance par joueur
Les fichiers image ne sont téléchargés que pour ce qui est réellement utilisé
(photos choisies pour les cartes, logos), via download_images().
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from . import scoring
from .leaguepedia import LeaguepediaClient, LeaguepediaError, chunked, in_clause, quote
from .prestige import apply_prestige, league_prestige, prestige_prior
from .models import (
    GameAppearance,
    League,
    Player,
    PlayerImage,
    PlayerSplitStats,
    Split,
    Team,
    Tournament,
)

logger = logging.getLogger(__name__)

# Nombre de valeurs par clause IN (limite la longueur des URLs).
IN_CHUNK = 40


#: La région Leaguepedia des compétitions internationales (MSI, Worlds…).
INTERNATIONAL_REGION = "International"


def is_international(region: str | None) -> bool:
    return (region or "").strip().lower() == INTERNATIONAL_REGION.lower()


def domestic_split_name(league_short: str, year) -> str:
    """La période d'une ligue locale : toute son année."""
    return f"{league_short} {year}".strip()


def default_client() -> LeaguepediaClient:
    return LeaguepediaClient(user_agent=settings.LEAGUEPEDIA_USER_AGENT, delay=settings.LEAGUEPEDIA_DELAY)


class LeaguepediaImporter:
    def __init__(self, client: LeaguepediaClient | None = None, log=None):
        self.client = client or default_client()
        self.log = log or logger.info
        #: Vrai quand le CDN des images refuse tout : on n'insiste plus.
        self.images_blocked = False
        self._image_failures = 0

    # ================================================================ Ligues
    def sync_leagues(self, names: list[str] | None = None, primary_only: bool = False) -> tuple[int, int]:
        """Les ligues de Leaguepedia (table Leagues) : celles nommées (nom
        court ou complet), ou les ligues officielles majeures, ou toutes.
        Renvoie (créées, mises à jour)."""
        where = []
        if names:
            where.append(f"({in_clause('L.League_Short', names)} OR {in_clause('L.League', names)})")
        if primary_only:
            where.append('L.Level="Primary"')
        rows = self.client.cargo(
            tables="Leagues=L",
            fields="L.League=League,L.League_Short=Short,L.Region=Region,L.Level=Level,L.IsOfficial=IsOfficial",
            where=" AND ".join(where) or None,
            order_by="L.League",
        )
        created = updated = 0
        now = timezone.now()
        for row in rows:
            name = (row.get("League") or "").strip()
            if not name:
                continue
            _, was_created = League.objects.update_or_create(
                name=name[:200],
                defaults={
                    "short_name": (row.get("Short") or "")[:50],
                    "region": (row.get("Region") or "")[:100],
                    "level": (row.get("Level") or "")[:50],
                    "is_official": scoring.parse_bool(row.get("IsOfficial")),
                    "synced_at": now,
                },
            )
            created += was_created
            updated += not was_created
        return created, updated

    # ================================================================ Tournois
    def discover(self, leagues: list[str], years: list[int] | None, name_like: str | None = None) -> list[dict]:
        """Les tournois de plusieurs ligues (noms complets) et années (None :
        toutes les années)."""
        where = [in_clause("T.League", leagues)]
        if years:
            where.append(in_clause("T.Year", [str(y) for y in years]))
        if name_like:
            where.append(f"T.Name LIKE {quote('%' + name_like + '%')}")
        return self.client.cargo(
            tables="Tournaments=T,Leagues=L",
            join_on="T.League=L.League",
            fields=(
                "T.Name=Name,T.OverviewPage=OverviewPage,T.DateStart=DateStart,T.Date=DateEnd,"
                "T.League=League,L.League_Short=LeagueShort,T.Region=Region,T.Year=Year,T.Split=Split,"
                "T.TournamentLevel=Level,T.IsPlayoffs=IsPlayoffs,T.IsQualifier=IsQualifier"
            ),
            where=" AND ".join(where),
            order_by="T.DateStart",
        )

    def discover_tournaments(self, league_short: str, year: int, name_like: str | None = None) -> list[dict]:
        where = [f"L.League_Short={quote(league_short)}", f"T.Year={quote(str(year))}"]
        if name_like:
            where.append(f"T.Name LIKE {quote('%' + name_like + '%')}")
        return self.client.cargo(
            tables="Tournaments=T,Leagues=L",
            join_on="T.League=L.League",
            fields=(
                "T.Name=Name,T.OverviewPage=OverviewPage,T.DateStart=DateStart,T.Date=DateEnd,"
                "T.League=League,L.League_Short=LeagueShort,T.Region=Region,T.Year=Year,T.Split=Split,"
                "T.TournamentLevel=Level,T.IsPlayoffs=IsPlayoffs,T.IsQualifier=IsQualifier"
            ),
            where=" AND ".join(where),
            order_by="T.DateStart",
        )

    @staticmethod
    def split_name_for(row: dict, region: str = "") -> str:
        """Nom de la période du jeu pour un tournoi.

        Ligue locale : toute l'année ensemble (« LCK 2026 » = LCK Cup,
        Rounds 1-2, 3-4 et playoffs), pour des notes sur assez de parties.
        Compétition internationale : chaque événement à part (« MSI 2025 »,
        ou le nom du tournoi quand Leaguepedia ne renseigne pas de split)."""
        label = (row.get("Split") or "").strip()
        short = row.get("LeagueShort") or row.get("League") or ""
        year = row.get("Year") or ""
        if not is_international(region or row.get("Region")):
            return domestic_split_name(short, year)
        if label:
            return f"{short} {year} {label}".strip()
        return row.get("Name") or row.get("OverviewPage")

    @transaction.atomic
    def save_tournaments(self, rows: list[dict], include_qualifiers: bool = False) -> list[Tournament]:
        saved = []
        for row in rows:
            league, _ = League.objects.get_or_create(
                name=row.get("League") or row.get("LeagueShort"),
                defaults={"short_name": row.get("LeagueShort", ""), "region": row.get("Region", "")},
            )
            is_qualifier = scoring.parse_bool(row.get("IsQualifier"))
            year = scoring.parse_int(row.get("Year"), default=None)
            start = scoring.parse_date(row.get("DateStart"))
            end = scoring.parse_date(row.get("DateEnd"))

            split = None
            if include_qualifiers or not is_qualifier:
                international = is_international(league.region or row.get("Region"))
                split_name = self.split_name_for(row, league.region)
                split, created = Split.objects.get_or_create(
                    name=split_name,
                    defaults={
                        "league": league,
                        "slug": slugify(split_name)[:200],
                        "year": year or (start.year if start else 0),
                        "split_label": row.get("Split", "") if international else "Saison",
                        "start_date": start,
                        "end_date": end,
                    },
                )
                # Élargit les dates du split avec celles de chaque tournoi.
                changed = False
                if start and (not split.start_date or start < split.start_date):
                    split.start_date, changed = start, True
                if end and (not split.end_date or end > split.end_date):
                    split.end_date, changed = end, True
                if changed and not created:
                    split.save(update_fields=["start_date", "end_date"])

            tournament, _ = Tournament.objects.update_or_create(
                overview_page=row["OverviewPage"],
                defaults={
                    "name": row.get("Name") or row["OverviewPage"],
                    "league": league,
                    "year": year,
                    "split_label": row.get("Split", ""),
                    "start_date": start,
                    "end_date": end,
                    "is_playoffs": scoring.parse_bool(row.get("IsPlayoffs")),
                    "is_qualifier": is_qualifier,
                    "level": row.get("Level", ""),
                },
            )
            # Ne pas écraser un rattachement fait à la main dans la gestion.
            if tournament.split_id is None and split is not None:
                tournament.split = split
                tournament.save(update_fields=["split"])
            saved.append(tournament)
        return saved

    # ================================================================ Parties
    def fetch_scoreboard(self, tournament: Tournament) -> list[dict]:
        return self.client.cargo(
            tables="ScoreboardPlayers=SP,ScoreboardGames=SG",
            join_on="SP.GameId=SG.GameId",
            fields=(
                "SP.GameId=GameId,SP.Link=Link,SP.Name=Name,SP.Team=Team,SP.TeamVs=TeamVs,SP.Role=Role,"
                "SP.Champion=Champion,SP.Kills=Kills,SP.Deaths=Deaths,SP.Assists=Assists,SP.Gold=Gold,"
                "SP.CS=CS,SP.DamageToChampions=Damage,SP.VisionScore=VisionScore,SP.TeamKills=TeamKills,"
                "SP.TeamGold=TeamGold,SP.PlayerWin=PlayerWin,SP.Side=Side,SP.DateTime_UTC=DateTimeUTC,"
                "SG.Gamelength_Number=GameLength"
            ),
            where=f"SP.OverviewPage={quote(tournament.overview_page)}",
            order_by="SP.UniqueLine",
        )

    def import_tournament_games(self, tournament: Tournament) -> int:
        rows = self.fetch_scoreboard(tournament)
        self.log(f"  {tournament.name} : {len(rows)} lignes de scoreboard")
        if not rows:
            return 0

        links = {r["Link"] or r["Name"]: r["Name"] for r in rows if r.get("Link") or r.get("Name")}
        players = self.sync_players(links)

        team_damage: dict[tuple, int] = defaultdict(int)
        for r in rows:
            team_damage[(r["GameId"], r["Team"])] += scoring.parse_int(r.get("Damage"))

        teams = {}
        for name in {r["Team"] for r in rows if r.get("Team")}:
            teams[name], _ = Team.objects.get_or_create(name=name)

        objs = []
        seen = set()
        for r in rows:
            player = players.get(r.get("Link") or r.get("Name"))
            key = (r["GameId"], player.pk if player else None)
            if player is None or key in seen:
                continue
            seen.add(key)
            vision = r.get("VisionScore")
            objs.append(
                GameAppearance(
                    game_id=r["GameId"],
                    tournament=tournament,
                    player=player,
                    team=teams.get(r.get("Team")),
                    opponent=r.get("TeamVs", ""),
                    role=scoring.normalize_role(r.get("Role")),
                    champion=r.get("Champion", ""),
                    side=str(r.get("Side", ""))[:10],
                    win=scoring.parse_bool(r.get("PlayerWin")),
                    kills=scoring.parse_int(r.get("Kills")),
                    deaths=scoring.parse_int(r.get("Deaths")),
                    assists=scoring.parse_int(r.get("Assists")),
                    gold=scoring.parse_int(r.get("Gold")),
                    cs=scoring.parse_int(r.get("CS")),
                    damage=scoring.parse_int(r.get("Damage")),
                    vision_score=scoring.parse_int(vision, default=None) if vision not in (None, "") else None,
                    team_kills=scoring.parse_int(r.get("TeamKills")),
                    team_gold=scoring.parse_int(r.get("TeamGold")),
                    team_damage=team_damage[(r["GameId"], r["Team"])],
                    game_length=scoring.parse_float(r.get("GameLength")),
                    played_at=scoring.parse_datetime_utc(r.get("DateTimeUTC")),
                )
            )

        with transaction.atomic():
            tournament.appearances.all().delete()
            GameAppearance.objects.bulk_create(objs, batch_size=500)
            tournament.games_imported = len({o.game_id for o in objs})
            tournament.last_imported_at = timezone.now()
            tournament.save(update_fields=["games_imported", "last_imported_at"])
        return len(objs)

    # ================================================================ Joueurs
    PLAYER_FIELDS = (
        "P.OverviewPage=OverviewPage,P.ID=IGN,P.Image=Image,P.Name=Name,P.NativeName=NativeName,"
        "P.Country=Country,P.Nationality=Nationality,P.Birthdate=Birthdate,P.Role=Role,P.Team=Team,"
        "P.IsRetired=IsRetired"
    )

    def sync_players(self, links: dict[str, str]) -> dict[str, Player]:
        """links : {nom de page utilisé dans le scoreboard: pseudo affiché}.
        Renvoie {lien: Player}, en créant/mettant à jour les joueurs."""
        found: dict[str, dict] = {}
        lower_links = {link.lower(): link for link in links}

        # 1) Via PlayerRedirects : gère les joueurs qui ont changé de pseudo.
        for batch in chunked(sorted(links), IN_CHUNK):
            try:
                rows = self.client.cargo(
                    tables="PlayerRedirects=PR,Players=P",
                    join_on="PR.OverviewPage=P.OverviewPage",
                    fields="PR.AllName=AllName," + self.PLAYER_FIELDS,
                    where=in_clause("PR.AllName", batch),
                )
            except LeaguepediaError as exc:
                self.log(f"  ! PlayerRedirects indisponible ({exc}), passage à la recherche directe")
                rows = []
            for row in rows:
                link = lower_links.get((row.get("AllName") or "").lower())
                if link:
                    found[link] = row

        # 2) Recherche directe sur Players pour ce qui reste.
        missing = [link for link in links if link not in found]
        for batch in chunked(missing, IN_CHUNK):
            rows = self.client.cargo(
                tables="Players=P", fields=self.PLAYER_FIELDS, where=in_clause("P.OverviewPage", batch)
            )
            for row in rows:
                link = lower_links.get((row.get("OverviewPage") or "").lower())
                if link:
                    found[link] = row

        result: dict[str, Player] = {}
        now = timezone.now()
        for link, display_name in links.items():
            row = found.get(link)
            if row is None:
                player, _ = Player.objects.get_or_create(
                    overview_page=link, defaults={"ign": display_name or link, "needs_review": True}
                )
                result[link] = player
                continue
            player, _ = Player.objects.update_or_create(
                overview_page=row["OverviewPage"],
                defaults={
                    "ign": (row.get("IGN") or display_name or row["OverviewPage"])[:100],
                    "real_name": (row.get("Name") or "")[:200],
                    "native_name": (row.get("NativeName") or "")[:200],
                    "birthdate": scoring.parse_date(row.get("Birthdate")),
                    "country": (row.get("Country") or "")[:100],
                    "nationality": (row.get("Nationality") or "")[:200],
                    "role": scoring.normalize_role(row.get("Role")),
                    "current_team": (row.get("Team") or "")[:200],
                    "is_retired": scoring.parse_bool(row.get("IsRetired")),
                    "image_filename": (row.get("Image") or "")[:255],
                    "needs_review": False,
                    "last_synced_at": now,
                },
            )
            result[link] = player
        unresolved = [link for link in links if link not in found]
        if unresolved:
            self.log(f"  ! {len(unresolved)} joueur(s) non trouvé(s), marqués « à vérifier » : {', '.join(unresolved[:10])}")
        return result

    def sync_player_images(self, players) -> int:
        pages = {p.overview_page: p for p in players}
        lower = {k.lower(): v for k, v in pages.items()}
        count = 0
        for batch in chunked(sorted(pages), IN_CHUNK):
            rows = self.client.cargo(
                tables="PlayerImages=PI",
                fields=(
                    "PI.FileName=FileName,PI.Link=Link,PI.Team=Team,PI.Tournament=Tournament,"
                    "PI.ImageType=ImageType,PI.Caption=Caption"
                ),
                where=in_clause("PI.Link", batch),
            )
            for row in rows:
                player = lower.get((row.get("Link") or "").lower())
                if not player or not row.get("FileName"):
                    continue
                PlayerImage.objects.update_or_create(
                    filename=row["FileName"],
                    defaults={
                        "player": player,
                        "tournament_name": row.get("Tournament", ""),
                        "team_name": row.get("Team", ""),
                        "image_type": row.get("ImageType", ""),
                        "caption": (row.get("Caption") or "")[:255],
                    },
                )
                count += 1
        return count

    def sync_team_logos(self, teams) -> None:
        names = {t.name: t for t in teams}
        for batch in chunked(sorted(names), IN_CHUNK):
            try:
                rows = self.client.cargo(
                    tables="Teams=T", fields="T.Name=Name,T.Short=Short,T.Image=Image", where=in_clause("T.Name", batch)
                )
            except LeaguepediaError as exc:
                self.log(f"  ! Logos d'équipes indisponibles : {exc}")
                return
            for row in rows:
                team = names.get(row.get("Name"))
                if team:
                    team.short_name = row.get("Short", "")[:20]
                    team.logo_filename = row.get("Image", "")
                    team.save(update_fields=["short_name", "logo_filename"])

    # ================================================================ Images
    #: Après autant d'images refusées d'affilée, on arrête d'essayer.
    MAX_IMAGE_FAILURES = 5

    def download_images(self, items: list[tuple]) -> int:
        """items : liste de (instance, nom du champ fichier Leaguepedia, nom du champ ImageField).
        Télécharge uniquement les fichiers absents en local."""
        todo = [
            (obj, getattr(obj, name_attr), file_attr)
            for obj, name_attr, file_attr in items
            if getattr(obj, name_attr) and not getattr(obj, file_attr)
        ]
        if not todo:
            return 0
        if self.images_blocked:
            self.log(f"! {len(todo)} image(s) ignorée(s) : le CDN de Fandom refuse les téléchargements.")
            return 0
        infos = self.client.image_infos({name for _, name, _ in todo}, width=settings.LEAGUEPEDIA_IMAGE_WIDTH)
        done = 0
        for index, (obj, name, file_attr) in enumerate(todo):
            info = infos.get(name)
            if not info:
                self.log(f"  ! Image introuvable sur Leaguepedia : {name}")
                continue
            try:
                content = self.client.download(info["thumb"], info.get("url"))
            except Exception as exc:  # noqa: BLE001 - on continue avec les autres images
                self._image_failures += 1
                self.log(f"  ! Téléchargement échoué ({name}) : {exc}")
                if self._image_failures >= self.MAX_IMAGE_FAILURES:
                    self.images_blocked = True
                    self.log(
                        f"! Le CDN de Fandom refuse toutes les images ({self._image_failures} échecs d'affilée) : "
                        f"les {len(todo) - index - 1} suivante(s) sont ignorées. Relancer plus tard avec "
                        f"l'action « Télécharger les photos manquantes » des splits."
                    )
                    break
                continue
            self._image_failures = 0
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
            getattr(obj, file_attr).save(safe, ContentFile(content), save=True)
            done += 1
        if done and self.client.image_strategy is not None:
            strategy = self.client._image_header_strategies()[self.client.image_strategy][0]
            self.log(f"  Images téléchargées avec : {strategy}")
        return done

    # ================================================================ Stats
    @staticmethod
    def _row(a: GameAppearance) -> dict:
        return {
            "kills": a.kills,
            "deaths": a.deaths,
            "assists": a.assists,
            "gold": a.gold,
            "cs": a.cs,
            "damage": a.damage,
            "vision_score": a.vision_score,
            "team_kills": a.team_kills,
            "team_gold": a.team_gold,
            "team_damage": a.team_damage,
            "game_length": a.game_length,
            "win": a.win,
            "role": a.role,
            "team": a.team_id,
            "champion": a.champion,
        }

    @classmethod
    def compute_split_stats(cls, split: Split) -> int:
        """Note chaque partie du split (par rapport à toutes les parties
        importées, rôle par rôle), puis agrège par joueur."""
        reference = scoring.reference(
            [cls._row(a) for a in GameAppearance.objects.only(
                "kills", "deaths", "assists", "gold", "cs", "damage", "vision_score", "team_kills",
                "team_gold", "team_damage", "game_length", "win", "role", "team_id", "champion",
            )]
        )
        appearances = list(
            GameAppearance.objects.filter(tournament__split=split).select_related("tournament__league").order_by("played_at")
        )
        # Le prestige de la ligue : la même partie vaut moins en ligue régionale qu'en LCK.
        split_prestige = league_prestige(split.league)
        by_player: dict[int, list[dict]] = defaultdict(list)
        for a in appearances:
            row = cls._row(a)
            raw, row["zs"] = scoring.game_rating(row, reference)
            a.rating = apply_prestige(raw, league_prestige(a.tournament.league))
            row["rating"] = a.rating
            by_player[a.player_id].append(row)
        GameAppearance.objects.bulk_update(appearances, ["rating"], batch_size=500)

        stats = []
        for player_id, rows in by_player.items():
            agg = scoring.aggregate_appearances(rows)
            agg["player_id"] = player_id
            agg["score"] = agg["rating"] = scoring.split_rating(
                [r["rating"] for r in rows], settings.GAME_RATING_PRIOR_GAMES, prior_mean=prestige_prior(split_prestige)
            )
            agg["dimensions"] = scoring.dimension_scores([r["zs"] for r in rows])
            stats.append(agg)
        scoring.rank_percentiles(stats)

        model_fields = {f.name for f in PlayerSplitStats._meta.get_fields()}
        with transaction.atomic():
            PlayerSplitStats.objects.filter(split=split).exclude(player_id__in=list(by_player)).delete()
            for s in stats:
                defaults = {k: v for k, v in s.items() if k in model_fields and k not in {"player", "team"}}
                defaults["team_id"] = s["team"]
                PlayerSplitStats.objects.update_or_create(player_id=s["player_id"], split=split, defaults=defaults)
        return len(stats)

    # ================================================================ Tout
    def import_split(self, split: Split, with_images: bool = True) -> dict:
        self.log(f"== {split.name}")
        tournaments = list(split.tournaments.all())
        if not tournaments:
            self.log("  Aucun tournoi rattaché à ce split (voir la gestion ou lp_tournaments --save).")
            return {"rows": 0}
        rows = sum(self.import_tournament_games(t) for t in tournaments)

        players = list(Player.objects.filter(appearances__tournament__split=split).distinct())
        teams = list(
            Team.objects.filter(pk__in=GameAppearance.objects.filter(tournament__split=split).values("team"))
        )
        images = self.sync_player_images(players)
        self.sync_team_logos(set(teams))
        n_stats = self.compute_split_stats(split)

        split.last_imported_at = timezone.now()
        split.save(update_fields=["last_imported_at"])
        self.log(f"  {rows} lignes, {len(players)} joueurs, {images} photos référencées, {n_stats} fiches de stats")

        if with_images:
            n = self.download_images([(t, "logo_filename", "logo") for t in set(teams)])
            self.log(f"  {n} logo(s) téléchargé(s)")
        return {"rows": rows, "players": len(players), "stats": n_stats}

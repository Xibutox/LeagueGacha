"""Données esport récupérées depuis Leaguepedia et stockées en local.

Hiérarchie :
    League (LEC) → Split (LEC 2025 Spring) → Tournament (saison régulière, playoffs…)
    GameAppearance = une ligne de scoreboard (un joueur dans une partie)
    PlayerSplitStats = agrégat des GameAppearance d'un joueur sur un split
"""
from django.db import models
from django.utils.text import slugify

from .storage import game_storage


class League(models.Model):
    name = models.CharField("nom", max_length=200, unique=True)
    short_name = models.CharField("nom court", max_length=50, blank=True)
    region = models.CharField("région", max_length=100, blank=True)
    level = models.CharField("niveau", max_length=50, blank=True, help_text="Primary, Secondary…")
    is_official = models.BooleanField("officielle", default=False)
    synced_at = models.DateTimeField("synchronisée le", null=True, blank=True)

    class Meta:
        verbose_name = "ligue"
        verbose_name_plural = "ligues"
        ordering = ["short_name", "name"]

    def __str__(self):
        return self.short_name or self.name


class Split(models.Model):
    """Un split au sens du jeu : regroupe un ou plusieurs tournois Leaguepedia
    (saison régulière + playoffs par exemple). Une carte = un joueur × un split."""

    league = models.ForeignKey(League, verbose_name="ligue", on_delete=models.CASCADE, related_name="splits")
    name = models.CharField("nom", max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    year = models.PositiveIntegerField("année")
    split_label = models.CharField("split", max_length=100, blank=True, help_text="Spring, Summer, Winter…")
    start_date = models.DateField("début", null=True, blank=True)
    end_date = models.DateField("fin", null=True, blank=True)
    is_playable = models.BooleanField(
        "jouable", default=True, help_text="Les cartes de ce split peuvent apparaître dans les boosters."
    )
    last_imported_at = models.DateTimeField("dernier import", null=True, blank=True)

    class Meta:
        verbose_name = "split"
        verbose_name_plural = "splits"
        ordering = ["-year", "league__short_name", "start_date", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:200]
        super().save(*args, **kwargs)


class Tournament(models.Model):
    """Un tournoi Leaguepedia (clé : OverviewPage, ex. « LEC/2025 Season/Spring Season »)."""

    overview_page = models.CharField("page Leaguepedia", max_length=255, unique=True)
    name = models.CharField("nom", max_length=255)
    league = models.ForeignKey(League, verbose_name="ligue", on_delete=models.CASCADE, related_name="tournaments")
    split = models.ForeignKey(
        Split, verbose_name="split", on_delete=models.SET_NULL, null=True, blank=True, related_name="tournaments"
    )
    year = models.PositiveIntegerField("année", null=True, blank=True)
    split_label = models.CharField("split (Leaguepedia)", max_length=100, blank=True)
    start_date = models.DateField("début", null=True, blank=True)
    end_date = models.DateField("fin", null=True, blank=True)
    is_playoffs = models.BooleanField("playoffs", default=False)
    is_qualifier = models.BooleanField("qualifications", default=False)
    level = models.CharField("niveau", max_length=50, blank=True)
    games_imported = models.PositiveIntegerField("parties importées", default=0)
    last_imported_at = models.DateTimeField("dernier import", null=True, blank=True)

    class Meta:
        verbose_name = "tournoi"
        verbose_name_plural = "tournois"
        ordering = ["-start_date", "name"]

    def __str__(self):
        return self.name


class Team(models.Model):
    name = models.CharField("nom", max_length=200, unique=True)
    short_name = models.CharField("abréviation", max_length=20, blank=True)
    logo_filename = models.CharField("fichier logo Leaguepedia", max_length=255, blank=True)
    logo = models.ImageField("logo", upload_to="teams/", storage=game_storage, blank=True)

    class Meta:
        verbose_name = "équipe"
        verbose_name_plural = "équipes"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Player(models.Model):
    ROLE_CHOICES = [
        ("Top", "Top"),
        ("Jungle", "Jungle"),
        ("Mid", "Mid"),
        ("Bot", "Bot"),
        ("Support", "Support"),
    ]

    overview_page = models.CharField(
        "page Leaguepedia", max_length=255, unique=True, help_text="Nom de la page du joueur, ex. « Caps »."
    )
    ign = models.CharField("pseudo", max_length=100, db_index=True)
    real_name = models.CharField("nom complet", max_length=200, blank=True)
    native_name = models.CharField("nom (écriture native)", max_length=200, blank=True)
    birthdate = models.DateField("date de naissance", null=True, blank=True)
    country = models.CharField("pays", max_length=100, blank=True, help_text="Champ « Country » de Leaguepedia.")
    nationality = models.CharField("nationalité(s)", max_length=200, blank=True)
    role = models.CharField("rôle", max_length=20, blank=True)
    current_team = models.CharField("équipe actuelle", max_length=200, blank=True)
    is_retired = models.BooleanField("retraité", default=False)
    image_filename = models.CharField("photo principale (Leaguepedia)", max_length=255, blank=True)
    photo = models.ImageField("photo principale", upload_to="players/", storage=game_storage, blank=True)
    needs_review = models.BooleanField(
        "à vérifier", default=False, help_text="Infos non trouvées automatiquement sur Leaguepedia."
    )
    last_synced_at = models.DateTimeField("dernière synchro", null=True, blank=True)

    class Meta:
        verbose_name = "joueur"
        verbose_name_plural = "joueurs"
        ordering = ["ign"]

    def __str__(self):
        return self.ign

    @property
    def birth_year(self):
        return self.birthdate.year if self.birthdate else None


class PlayerImage(models.Model):
    """Une photo Leaguepedia d'un joueur, généralement liée à un tournoi."""

    player = models.ForeignKey(Player, verbose_name="joueur", on_delete=models.CASCADE, related_name="images")
    filename = models.CharField("fichier Leaguepedia", max_length=255, unique=True)
    tournament_name = models.CharField("tournoi", max_length=255, blank=True)
    team_name = models.CharField("équipe", max_length=200, blank=True)
    image_type = models.CharField("type", max_length=100, blank=True)
    caption = models.CharField("légende", max_length=255, blank=True)
    file = models.ImageField("fichier local", upload_to="players/", storage=game_storage, blank=True)

    class Meta:
        verbose_name = "photo de joueur"
        verbose_name_plural = "photos de joueurs"
        ordering = ["player__ign", "-tournament_name"]

    def __str__(self):
        return self.filename


class GameAppearance(models.Model):
    """Une ligne de scoreboard : un joueur dans une partie."""

    game_id = models.CharField("partie (GameId)", max_length=255)
    tournament = models.ForeignKey(
        Tournament, verbose_name="tournoi", on_delete=models.CASCADE, related_name="appearances"
    )
    player = models.ForeignKey(Player, verbose_name="joueur", on_delete=models.CASCADE, related_name="appearances")
    team = models.ForeignKey(
        Team, verbose_name="équipe", on_delete=models.SET_NULL, null=True, related_name="appearances"
    )
    opponent = models.CharField("adversaire", max_length=200, blank=True)
    role = models.CharField("rôle", max_length=20, blank=True)
    champion = models.CharField("champion", max_length=100, blank=True)
    side = models.CharField("côté", max_length=10, blank=True)
    win = models.BooleanField("victoire", default=False)
    kills = models.PositiveIntegerField("kills", default=0)
    deaths = models.PositiveIntegerField("morts", default=0)
    assists = models.PositiveIntegerField("assists", default=0)
    gold = models.PositiveIntegerField("gold", default=0)
    cs = models.PositiveIntegerField("CS", default=0)
    damage = models.PositiveIntegerField("dégâts aux champions", default=0)
    vision_score = models.PositiveIntegerField("score de vision", null=True, blank=True)
    team_kills = models.PositiveIntegerField("kills de l'équipe", default=0)
    team_gold = models.PositiveIntegerField("gold de l'équipe", default=0)
    team_damage = models.PositiveIntegerField("dégâts de l'équipe", default=0)
    game_length = models.FloatField("durée (min)", null=True, blank=True)
    rating = models.FloatField(
        "note de la partie", null=True, blank=True, help_text="0–100 ; 50 = partie moyenne d'un pro du même rôle."
    )
    played_at = models.DateTimeField("date", null=True, blank=True)

    class Meta:
        verbose_name = "ligne de scoreboard"
        verbose_name_plural = "lignes de scoreboard"
        ordering = ["-played_at"]
        constraints = [models.UniqueConstraint(fields=["game_id", "player"], name="unique_player_per_game")]

    def __str__(self):
        return f"{self.player} — {self.champion} ({self.game_id})"


class PlayerSplitStats(models.Model):
    """Statistiques agrégées d'un joueur sur un split + note calculée."""

    player = models.ForeignKey(Player, verbose_name="joueur", on_delete=models.CASCADE, related_name="split_stats")
    split = models.ForeignKey(Split, verbose_name="split", on_delete=models.CASCADE, related_name="player_stats")
    team = models.ForeignKey(
        Team, verbose_name="équipe", on_delete=models.SET_NULL, null=True, related_name="split_stats"
    )
    role = models.CharField("rôle", max_length=20, blank=True)
    games = models.PositiveIntegerField("parties", default=0)
    wins = models.PositiveIntegerField("victoires", default=0)
    kills = models.PositiveIntegerField("kills", default=0)
    deaths = models.PositiveIntegerField("morts", default=0)
    assists = models.PositiveIntegerField("assists", default=0)
    win_rate = models.FloatField("% victoires", default=0)
    kda = models.FloatField("KDA", default=0)
    kill_participation = models.FloatField("KP", default=0)
    cs_per_min = models.FloatField("CS/min", default=0)
    gold_per_min = models.FloatField("gold/min", default=0)
    damage_per_min = models.FloatField("DPM", default=0)
    vision_per_min = models.FloatField("vision/min", default=0)
    damage_share = models.FloatField("part des dégâts", default=0)
    gold_share = models.FloatField("part du gold", default=0)
    champions = models.JSONField("champions joués", default=list, blank=True)
    score = models.FloatField(
        "note", default=0, help_text="0–100 ; 50 = pro moyen du même rôle (moyenne des parties, pondérée)."
    )
    dimensions = models.JSONField(
        "notes par dimension", default=dict, blank=True, help_text="Combat, impact, dégâts, farm, vision, victoire."
    )
    percentile = models.FloatField("percentile", default=0, help_text="Rang dans le split, 0–100.")
    computed_at = models.DateTimeField("calculé le", auto_now=True)

    class Meta:
        verbose_name = "stats joueur × split"
        verbose_name_plural = "stats joueurs × splits"
        ordering = ["split", "-percentile"]
        constraints = [models.UniqueConstraint(fields=["player", "split"], name="unique_stats_per_split")]

    def __str__(self):
        return f"{self.player} — {self.split}"


class Champion(models.Model):
    """Un champion de LoL : créé à l'import à partir des scoreboards, puis
    complété par Data Dragon (titre, classes, profil, images)."""

    name = models.CharField("nom", max_length=60, unique=True)
    key = models.CharField(
        "identifiant Riot",
        max_length=60,
        blank=True,
        help_text="Sert aux images (ex. MonkeyKing pour Wukong). Calculé à l'import, corrigeable.",
    )
    title = models.CharField("titre", max_length=120, blank=True)
    tags = models.JSONField(
        "classes", default=list, blank=True, help_text="Fighter, Tank, Mage, Assassin, Marksman, Support."
    )
    attack = models.PositiveSmallIntegerField("attaque", default=0, help_text="0–10, Data Dragon.")
    defense = models.PositiveSmallIntegerField("défense", default=0)
    magic = models.PositiveSmallIntegerField("magie", default=0)
    difficulty = models.PositiveSmallIntegerField("difficulté", default=0)
    icon = models.ImageField("icône", upload_to="champions/icons/", storage=game_storage, blank=True)
    portrait = models.ImageField(
        "illustration", upload_to="champions/portraits/", storage=game_storage, blank=True,
        help_text="L'illustration en hauteur de l'écran de chargement.",
    )
    splash = models.ImageField(
        "splash", upload_to="champions/splashes/", storage=game_storage, blank=True,
        help_text="L'illustration en largeur, centrée.",
    )
    synced_at = models.DateTimeField("Data Dragon le", null=True, blank=True)

    class Meta:
        verbose_name = "champion"
        verbose_name_plural = "champions"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def icon_url(self) -> str:
        if self.icon:
            return self.icon.url
        from .champions import icon_url

        return icon_url(self.key)

    @property
    def portrait_url(self) -> str:
        if self.portrait:
            return self.portrait.url
        from .champions import portrait_url

        return portrait_url(self.key)

    @property
    def splash_url(self) -> str:
        if self.splash:
            return self.splash.url
        from .champions import splash_url

        return splash_url(self.key)

    @property
    def profile(self) -> dict:
        """Ce qui sert au calcul de cohérence d'une composition."""
        return {
            "name": self.name,
            "tags": list(self.tags or []),
            "attack": self.attack,
            "defense": self.defense,
            "magic": self.magic,
        }

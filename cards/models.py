"""Cartes, raretés, boosters et collections des joueurs du site."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify

import re

from esport.models import Champion, League, Player, PlayerImage, PlayerSplitStats, Split, Team
from esport.storage import game_storage

#: Les formats acceptés pour le visuel d'un booster.
BOOSTER_IMAGE_FORMATS = ["png", "jpg", "jpeg", "webp", "gif"]


class Rarity(models.Model):
    """Niveau de rareté. Entièrement paramétrable dans la gestion.

    Chaque joueur existe à toutes les raretés : la rareté est celle de
    l'exemplaire possédé, pas de la carte.

    - `drop_rate` : chance (en %) qu'un emplacement de booster soit de cette
      rareté. Les taux sont ramenés à 100 % s'ils n'y font pas exactement.
    - `score_bonus` : bonus (en %) au score d'un poste d'équipe.
    - `fusion_cost` : exemplaires à fusionner pour passer à la rareté suivante.
    - `exchange_cost` / `exchange_rarity` : un doublon de cette rareté
      s'échange contre le joueur de son choix, à la rareté d'échange.
    """

    code = models.SlugField("code", max_length=30, unique=True)
    name = models.CharField("nom", max_length=50)
    order = models.PositiveSmallIntegerField("ordre", default=0, help_text="0 = la plus commune.")
    color = models.CharField("couleur", max_length=7, default="#9aa4b1", help_text="Hexadécimal, ex. #f5b301")
    drop_rate = models.DecimalField(
        "taux de tirage (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Chance qu'un emplacement de booster soit de cette rareté.",
    )
    score_bonus = models.DecimalField(
        "bonus de score (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("200"))],
        help_text="Ajouté au score d'un poste d'équipe tenu par un exemplaire de cette rareté.",
    )
    fusion_cost = models.PositiveSmallIntegerField(
        "exemplaires pour fusionner",
        default=2,
        help_text="Combien d'exemplaires donnent 1 exemplaire de la rareté suivante. 0 = pas de fusion.",
    )
    exchange_cost = models.PositiveSmallIntegerField(
        "doublons pour un échange",
        default=0,
        help_text=(
            "Doublons (en plus de l'exemplaire gardé) à donner pour choisir n'importe quel "
            "joueur à la rareté d'échange. 0 = pas d'échange."
        ),
    )
    exchange_rarity = models.ForeignKey(
        "self",
        verbose_name="rareté reçue à l'échange",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "rareté"
        verbose_name_plural = "raretés"
        ordering = ["order"]

    def __str__(self):
        return self.name

    @property
    def bonus_factor(self) -> float:
        return 1 + float(self.score_bonus) / 100

    def next(self):
        return Rarity.objects.filter(order__gt=self.order).order_by("order").first()


class Card(models.Model):
    """Une carte = un joueur sur un split."""

    player = models.ForeignKey(Player, verbose_name="joueur", on_delete=models.CASCADE, related_name="cards")
    split = models.ForeignKey(Split, verbose_name="split", on_delete=models.CASCADE, related_name="cards")
    stats = models.OneToOneField(
        PlayerSplitStats, verbose_name="stats", on_delete=models.SET_NULL, null=True, blank=True, related_name="card"
    )
    team = models.ForeignKey(
        Team, verbose_name="équipe", on_delete=models.SET_NULL, null=True, blank=True, related_name="cards"
    )
    role = models.CharField("rôle", max_length=20, blank=True)
    overall = models.PositiveSmallIntegerField("note", default=60)
    image = models.ForeignKey(
        PlayerImage, verbose_name="photo", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    image_locked = models.BooleanField("photo verrouillée", default=False)
    is_active = models.BooleanField("active", default=True, help_text="Peut sortir des boosters.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "carte"
        verbose_name_plural = "cartes"
        ordering = ["split", "-overall", "player__ign"]
        constraints = [models.UniqueConstraint(fields=["player", "split"], name="unique_card_per_player_split")]

    def __str__(self):
        return f"{self.player.ign} — {self.split.name}"

    @property
    def image_url(self):
        if self.image and self.image.file:
            return self.image.file.url
        if self.player.photo:
            return self.player.photo.url
        return ""


class Booster(models.Model):
    name = models.CharField("nom", max_length=100)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    description = models.TextField("description", blank=True)
    # Le contenu : les cartes des splits jouables qui passent TOUS les
    # filtres renseignés (un filtre vide ne filtre pas).
    leagues = models.ManyToManyField(
        League,
        verbose_name="ligues",
        blank=True,
        related_name="boosters",
        help_text="Vide = toutes les ligues.",
    )
    years = models.CharField(
        "années",
        max_length=200,
        blank=True,
        help_text="Ex. « 2025, 2026 ». Vide = toutes les années.",
    )
    splits = models.ManyToManyField(
        Split,
        verbose_name="splits",
        blank=True,
        related_name="boosters",
        help_text="Vide = tous les splits.",
    )
    qualified_teams_only = models.BooleanField(
        "équipes qualifiées à l'international seulement",
        default=False,
        help_text=(
            "Seulement les joueurs des équipes qui ont joué un First Stand, un MSI ou des "
            "Worlds la même année."
        ),
    )
    auto = models.BooleanField(
        "créé automatiquement",
        default=False,
        help_text="Booster type créé par l'import (init_boosters).",
    )
    cards_per_pack = models.PositiveSmallIntegerField("cartes par booster", default=5)
    guaranteed_rarity = models.ForeignKey(
        Rarity,
        verbose_name="rareté garantie (dernière carte)",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="La dernière carte sera au moins de cette rareté.",
    )
    cooldown_hours = models.DecimalField(
        "un booster toutes les (heures)",
        max_digits=6,
        decimal_places=2,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.05"))],
        help_text="Chaque joueur du site reçoit un booster de ce type à ce rythme.",
    )
    max_stock = models.PositiveSmallIntegerField(
        "stock maximum",
        default=24,
        help_text="Boosters gardés en réserve au plus ; au-delà, ils sont perdus.",
    )
    starting_stock = models.PositiveSmallIntegerField(
        "stock de départ", default=3, help_text="Boosters offerts à un nouveau joueur."
    )
    image = models.ImageField(
        "visuel",
        upload_to="boosters/",
        storage=game_storage,
        blank=True,
        validators=[FileExtensionValidator(BOOSTER_IMAGE_FORMATS)],
        help_text=(
            "PNG, JPEG, WebP ou GIF. Affiché sur le paquet, en hauteur (format 5 × 7, "
            "par exemple 500 × 700 px) ; le haut et le bas peuvent être rognés. "
            "Sans visuel : un dégradé de la couleur ci-dessous."
        ),
    )
    color = models.CharField("couleur", max_length=7, default="#1f6feb")
    is_active = models.BooleanField("actif", default=True)
    order = models.PositiveSmallIntegerField("ordre d'affichage", default=0)

    class Meta:
        verbose_name = "booster"
        verbose_name_plural = "boosters"
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:100]
        super().save(*args, **kwargs)

    def year_list(self) -> list[int]:
        return sorted({int(y) for y in re.findall(r"\d{4}", self.years or "")})

    def card_pool(self):
        qs = Card.objects.filter(is_active=True, split__is_playable=True)
        years = self.year_list()
        if years:
            qs = qs.filter(split__year__in=years)
        if self.pk:
            if self.leagues.exists():
                qs = qs.filter(split__league__in=self.leagues.all())
            if self.splits.exists():
                qs = qs.filter(split__in=self.splits.all())
        if self.qualified_teams_only:
            qs = qs.filter(qualified_for_international())
        return qs


def qualified_for_international():
    """Filtre de cartes : l'équipe de la carte a joué un First Stand, un MSI
    ou des Worlds la même année."""
    from esport.catalog import riot_events_q, side_events_q
    from esport.models import GameAppearance

    return models.Exists(
        GameAppearance.objects.filter(
            team_id=models.OuterRef("team_id"),
            tournament__split__year=models.OuterRef("split__year"),
            tournament__league__in=League.objects.filter(riot_events_q()),
        ).exclude(side_events_q("tournament__"))
    )


class BoosterRarityWeight(models.Model):
    """Optionnel : remplace le taux de tirage d'une rareté pour un booster donné."""

    booster = models.ForeignKey(
        Booster, verbose_name="booster", on_delete=models.CASCADE, related_name="rarity_weights"
    )
    rarity = models.ForeignKey(
        Rarity, verbose_name="rareté", on_delete=models.CASCADE, related_name="booster_weights"
    )
    rate = models.DecimalField(
        "taux de tirage (%)",
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )

    def __str__(self):
        return f"{self.booster} — {self.rarity} : {self.rate} %"

    class Meta:
        verbose_name = "taux de rareté du booster"
        verbose_name_plural = "taux de rareté du booster"
        constraints = [models.UniqueConstraint(fields=["booster", "rarity"], name="unique_weight_per_booster")]


class BoosterStock(models.Model):
    """La réserve de boosters d'un joueur du site, pour un booster.

    `stock` est la réserve à l'instant `refilled_at` ; la réserve réelle y
    ajoute un booster par période écoulée depuis, sans dépasser le maximum
    (voir services.available_packs)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE, related_name="booster_stocks"
    )
    booster = models.ForeignKey(Booster, verbose_name="booster", on_delete=models.CASCADE, related_name="stocks")
    stock = models.PositiveSmallIntegerField("réserve", default=0)
    refilled_at = models.DateTimeField("compté le")
    bonus = models.PositiveIntegerField(
        "boosters offerts",
        default=0,
        help_text="Offerts par l'administration : en plus de la réserve, sans plafond ni recharge.",
    )

    class Meta:
        verbose_name = "réserve de boosters"
        verbose_name_plural = "réserves de boosters"
        constraints = [models.UniqueConstraint(fields=["user", "booster"], name="unique_stock_per_booster")]

    def __str__(self):
        return f"{self.user} — {self.booster} : {self.stock}"


class BoosterGift(models.Model):
    """Des boosters offerts à un joueur du site par l'administration."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE, related_name="booster_gifts"
    )
    booster = models.ForeignKey(Booster, verbose_name="booster", on_delete=models.CASCADE, related_name="gifts")
    quantity = models.PositiveSmallIntegerField("boosters", default=1)
    message = models.CharField("message", max_length=200, blank=True)
    given_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="offert par",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField("offert le", auto_now_add=True)
    seen_at = models.DateTimeField("vu le", null=True, blank=True)

    class Meta:
        verbose_name = "cadeau de boosters"
        verbose_name_plural = "cadeaux de boosters"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.quantity} × {self.booster}"


class UserCard(models.Model):
    """Des exemplaires d'une carte, à une rareté, possédés par un utilisateur."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE, related_name="collection"
    )
    card = models.ForeignKey(Card, verbose_name="carte", on_delete=models.CASCADE, related_name="owners")
    rarity = models.ForeignKey(Rarity, verbose_name="rareté", on_delete=models.PROTECT, related_name="copies")
    quantity = models.PositiveIntegerField("exemplaires", default=1)
    first_obtained_at = models.DateTimeField("obtenue le", auto_now_add=True)
    last_obtained_at = models.DateTimeField("dernière obtention", auto_now=True)

    class Meta:
        verbose_name = "carte possédée"
        verbose_name_plural = "cartes possédées"
        constraints = [models.UniqueConstraint(fields=["user", "card", "rarity"], name="unique_usercard_rarity")]

    def __str__(self):
        return f"{self.user} — {self.card} ({self.rarity}) ×{self.quantity}"


class Portal(models.Model):
    """Un portail : un booster ouvrable pendant un temps limité, hors
    réserve. Chaque joueur du site y ouvre au plus `max_openings` boosters :
    le premier tout de suite, le suivant `wait_hours` après le précédent.

    Le portail du jour (auto) dure 24 h et tourne entre les boosters
    inactifs ; les autres se créent à la main dans la gestion (1 à 30 jours)."""

    MIN_DAYS = 1
    MAX_DAYS = 30

    name = models.CharField("nom", max_length=100, blank=True, help_text="Vide : « Portail · nom du booster ».")
    booster = models.ForeignKey(Booster, verbose_name="booster", on_delete=models.CASCADE, related_name="portals")
    starts_at = models.DateTimeField("ouverture")
    ends_at = models.DateTimeField("fermeture")
    max_openings = models.PositiveSmallIntegerField(
        "boosters par joueur", default=2, validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
    wait_hours = models.DecimalField(
        "attente entre deux boosters (heures)",
        max_digits=6,
        decimal_places=2,
        default=Decimal("12"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Le premier booster s'ouvre tout de suite ; le suivant, ce délai après le précédent.",
    )
    auto = models.BooleanField("portail du jour", default=False, help_text="Créé automatiquement chaque jour.")
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "portail"
        verbose_name_plural = "portails"
        ordering = ["-starts_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["starts_at"], condition=models.Q(auto=True), name="one_daily_portal_per_day"
            )
        ]

    def __str__(self):
        return self.title

    @property
    def title(self) -> str:
        return self.name or f"Portail · {self.booster.name}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.starts_at and self.ends_at:
            if self.ends_at <= self.starts_at:
                raise ValidationError({"ends_at": "La fermeture doit suivre l'ouverture."})
            if not self.auto:
                days = (self.ends_at - self.starts_at).total_seconds() / 86400
                if days < self.MIN_DAYS - 1e-6 or days > self.MAX_DAYS + 1e-6:
                    raise ValidationError(
                        {"ends_at": f"Un portail dure de {self.MIN_DAYS} à {self.MAX_DAYS} jours."}
                    )


class PackOpening(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE, related_name="pack_openings"
    )
    booster = models.ForeignKey(
        Booster, verbose_name="booster", on_delete=models.SET_NULL, null=True, related_name="openings"
    )
    opened_at = models.DateTimeField("ouvert le", auto_now_add=True, db_index=True)
    portal = models.ForeignKey(
        Portal, verbose_name="portail", on_delete=models.SET_NULL, null=True, blank=True, related_name="openings"
    )

    class Meta:
        verbose_name = "ouverture de booster"
        verbose_name_plural = "ouvertures de boosters"
        ordering = ["-opened_at"]

    def __str__(self):
        return f"{self.user} — {self.booster} ({self.opened_at:%d/%m/%Y %H:%M})"


class PackOpeningCard(models.Model):
    opening = models.ForeignKey(
        PackOpening, verbose_name="ouverture", on_delete=models.CASCADE, related_name="cards"
    )
    card = models.ForeignKey(Card, verbose_name="carte", on_delete=models.CASCADE, related_name="pulls")
    rarity = models.ForeignKey(Rarity, verbose_name="rareté", on_delete=models.PROTECT, related_name="pulls")
    slot = models.PositiveSmallIntegerField("position")
    was_new = models.BooleanField("nouvelle", default=False)

    class Meta:
        verbose_name = "carte tirée"
        verbose_name_plural = "cartes tirées"
        ordering = ["opening", "slot"]

    def __str__(self):
        return f"{self.opening} #{self.slot} : {self.card}"


ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"]


#: Nombre de compositions d'une équipe : chaque joueur reçoit autant de champions.
COMPOSITIONS = 5


class Roster(models.Model):
    """Une équipe de 5 cartes (une par rôle) montée par un joueur du site,
    avec 5 champions par joueur : la composition n réunit le n-ième
    champion de chaque joueur."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE, related_name="rosters"
    )
    name = models.CharField("nom", max_length=60)
    week = models.DateField(
        "semaine",
        null=True,
        blank=True,
        db_index=True,
        help_text="Vide : une équipe permanente. Sinon l'équipe de la semaine qui commence ce lundi (méta de la semaine).",
    )
    score = models.FloatField(
        "score",
        null=True,
        blank=True,
        db_index=True,
        help_text="Moyenne des 5 compositions ; seulement quand l'équipe est complète (5 joueurs, 25 champions).",
    )
    details = models.JSONField(
        "détail du score", default=dict, blank=True, help_text="Scores et cohérence de chaque composition."
    )
    created_at = models.DateTimeField("créée le", auto_now_add=True)
    updated_at = models.DateTimeField("modifiée le", auto_now=True)

    class Meta:
        verbose_name = "équipe"
        verbose_name_plural = "équipes"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "week"], condition=models.Q(week__isnull=False),
                                    name="one_weekly_roster_per_user"),
        ]

    def __str__(self):
        return f"{self.name} ({self.user})"

    def slot_map(self) -> dict:
        return {slot.role: slot for slot in self.slots.all()}

    @property
    def is_complete(self) -> bool:
        return len(self.slot_map()) == len(ROLES)

    @property
    def overall(self) -> int | None:
        """Note de l'équipe : moyenne des notes de ses cartes."""
        cards = [slot.card for slot in self.slots.all()]
        if not cards:
            return None
        return round(sum(c.overall for c in cards) / len(cards))

    @property
    def compositions(self) -> list:
        return self.details.get("compositions", []) if self.details else []


class RosterSlot(models.Model):
    roster = models.ForeignKey(Roster, verbose_name="équipe", on_delete=models.CASCADE, related_name="slots")
    role = models.CharField("rôle", max_length=20, choices=[(r, r) for r in ROLES])
    card = models.ForeignKey(Card, verbose_name="carte", on_delete=models.CASCADE, related_name="roster_slots")
    score = models.FloatField("score moyen du poste", null=True, blank=True)

    class Meta:
        verbose_name = "poste"
        verbose_name_plural = "postes"
        ordering = ["roster", "role"]
        constraints = [models.UniqueConstraint(fields=["roster", "role"], name="one_card_per_role")]

    def __str__(self):
        return f"{self.roster.name} — {self.role} : {self.card}"

    def champion_list(self) -> list:
        picks = {p.composition: p.champion for p in self.picks.all()}
        return [picks.get(i) for i in range(1, COMPOSITIONS + 1)]


class RosterPick(models.Model):
    """Le champion d'un joueur dans une composition de l'équipe."""

    slot = models.ForeignKey(RosterSlot, verbose_name="poste", on_delete=models.CASCADE, related_name="picks")
    composition = models.PositiveSmallIntegerField("composition")
    champion = models.ForeignKey(
        Champion, verbose_name="champion", on_delete=models.CASCADE, related_name="roster_picks"
    )
    score = models.FloatField("score", null=True, blank=True)

    class Meta:
        verbose_name = "champion choisi"
        verbose_name_plural = "champions choisis"
        ordering = ["slot", "composition"]
        constraints = [
            models.UniqueConstraint(fields=["slot", "composition"], name="one_champion_per_composition"),
            models.UniqueConstraint(fields=["slot", "champion"], name="distinct_champions_per_player"),
        ]

    def __str__(self):
        return f"{self.slot} — compo {self.composition} : {self.champion}"


class CardChampion(models.Model):
    """Ce qu'un joueur a fait avec un champion pendant le split de la carte :
    c'est ce qui permet de l'associer à ce champion dans une équipe."""

    card = models.ForeignKey(Card, verbose_name="carte", on_delete=models.CASCADE, related_name="champions")
    champion = models.ForeignKey(
        Champion, verbose_name="champion", on_delete=models.CASCADE, related_name="card_affinities"
    )
    games = models.PositiveIntegerField("parties", default=0)
    wins = models.PositiveIntegerField("victoires", default=0)
    history_games = models.PositiveIntegerField(
        "parties avant", default=0, help_text="Sur ce champion, dans les périodes précédentes."
    )
    history_wins = models.PositiveIntegerField("victoires avant", default=0)
    kda = models.FloatField("KDA", default=0)
    rating = models.FloatField(
        "note sur le champion",
        default=50,
        help_text=(
            "0–100 : ses parties sur ce champion pendant la période, plus son historique dessus "
            "(compté à moitié), tirées vers sa note quand il y en a peu."
        ),
    )

    class Meta:
        verbose_name = "champion joué"
        verbose_name_plural = "champions joués"
        ordering = ["card", "-games", "-rating"]
        constraints = [models.UniqueConstraint(fields=["card", "champion"], name="unique_card_champion")]

    def __str__(self):
        return f"{self.card} — {self.champion} ({self.games})"

    @property
    def win_rate(self) -> float:
        return round(100 * self.wins / self.games, 1) if self.games else 0.0


class ExchangeOffer(models.Model):
    """Un échange de doublon en cours : le doublon est déjà donné, le joueur
    choisit une carte parmi celles tirées au hasard (gardées ici : recharger
    la page ne relance pas le tirage)."""

    CHOICES = 5

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE, related_name="exchange_offers"
    )
    given_card = models.ForeignKey(
        Card, verbose_name="carte donnée", on_delete=models.SET_NULL, null=True, related_name="+"
    )
    given_rarity = models.ForeignKey(Rarity, verbose_name="rareté donnée", on_delete=models.PROTECT, related_name="+")
    rarity = models.ForeignKey(Rarity, verbose_name="rareté reçue", on_delete=models.PROTECT, related_name="+")
    choices = models.ManyToManyField(Card, verbose_name="cartes proposées", related_name="+")
    chosen = models.ForeignKey(
        Card, verbose_name="carte choisie", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField("proposé le", auto_now_add=True)
    chosen_at = models.DateTimeField("choisi le", null=True, blank=True)

    class Meta:
        verbose_name = "échange de doublon"
        verbose_name_plural = "échanges de doublons"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.given_card} ({self.created_at:%d/%m/%Y %H:%M})"


# --------------------------------------------------------------------- Tournois
class Tournament(models.Model):
    """Un tournoi international du passé, rejoué pendant quelques jours :
    chaque joueur du site affronte ses équipes, de la dernière à la
    gagnante, en Bo5 avec draft Fearless. Une défaite n'élimine pas : on
    retente après l'attente (`cooldown_hours`, entre deux Bo5)."""

    name = models.CharField("nom", max_length=120, blank=True, help_text="Vide : le nom du tournoi.")
    split = models.ForeignKey(
        "esport.Split", verbose_name="tournoi rejoué", on_delete=models.CASCADE, related_name="game_tournaments"
    )
    starts_at = models.DateTimeField("ouverture")
    ends_at = models.DateTimeField("fermeture")
    cooldown_hours = models.DecimalField(
        "attente entre deux Bo5 (heures)", max_digits=5, decimal_places=2, default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    opponents = models.JSONField(
        "adversaires", default=list, blank=True,
        help_text="Les équipes, de la moins bien classée à la gagnante (calculé à la création).",
    )
    auto = models.BooleanField("créé automatiquement", default=False)
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "tournoi"
        verbose_name_plural = "tournois"
        ordering = ["-starts_at"]

    def __str__(self):
        return self.title

    @property
    def title(self) -> str:
        return self.name or self.split.name


class TournamentRun(models.Model):
    """La progression d'un joueur du site dans un tournoi."""

    tournament = models.ForeignKey(Tournament, verbose_name="tournoi", on_delete=models.CASCADE, related_name="runs")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE, related_name="tournament_runs"
    )
    step = models.PositiveSmallIntegerField("adversaires battus", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "parcours de tournoi"
        verbose_name_plural = "parcours de tournoi"
        constraints = [models.UniqueConstraint(fields=["tournament", "user"], name="one_run_per_tournament")]

    def __str__(self):
        return f"{self.user} — {self.tournament} ({self.step})"

    @property
    def finished(self) -> bool:
        return self.step >= len(self.tournament.opponents)


class TournamentSeries(models.Model):
    """Un Bo5 contre une équipe du tournoi. Les parties sont gardées dans
    `games` : une entrée par partie, avec les champions choisis
    (user_picks, opponent_picks : {poste: id}), le détail des postes
    (user, opponent), les scores et le vainqueur (won)."""

    class Status(models.TextChoices):
        LINEUP = "lineup", "choix des joueurs"
        DRAFT = "draft", "en cours"
        WON = "won", "gagné"
        LOST = "lost", "perdu"

    WINS_NEEDED = 3

    run = models.ForeignKey(TournamentRun, verbose_name="parcours", on_delete=models.CASCADE, related_name="series")
    step = models.PositiveSmallIntegerField("rang de l'adversaire")
    team = models.ForeignKey("esport.Team", verbose_name="adversaire", on_delete=models.SET_NULL, null=True)
    status = models.CharField("état", max_length=10, choices=Status.choices, default=Status.LINEUP)
    opponent = models.JSONField("joueurs adverses", default=dict, help_text="{poste: id de carte}")
    opponent_rarity = models.ForeignKey(Rarity, verbose_name="rareté adverse", on_delete=models.PROTECT, related_name="+")
    lineup = models.JSONField("joueurs alignés", default=dict, blank=True, help_text="{poste: id de carte}")
    lineup_rarities = models.JSONField("raretés alignées", default=dict, blank=True)
    games = models.JSONField("parties", default=list, blank=True)
    prepared = models.JSONField(
        "préparé", default=dict, blank=True,
        help_text="Calculé une fois par Bo5 : les champions de chaque joueur adverse pendant le tournoi, "
                  "les cibles de ban de l'adversaire.",
    )
    draft = models.JSONField("draft en cours", default=dict, blank=True,
                             help_text='{"side": "blue"|"red", "actions": [{team, type, champion, role}]}')
    wins = models.PositiveSmallIntegerField("victoires", default=0)
    losses = models.PositiveSmallIntegerField("défaites", default=0)
    reward_card = models.ForeignKey(Card, verbose_name="carte gagnée", on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name="+")
    reward_rarity = models.ForeignKey(Rarity, verbose_name="rareté gagnée", on_delete=models.SET_NULL, null=True,
                                      blank=True, related_name="+")
    created_at = models.DateTimeField("commencé le", auto_now_add=True)
    finished_at = models.DateTimeField("terminé le", null=True, blank=True)

    class Meta:
        verbose_name = "Bo5 de tournoi"
        verbose_name_plural = "Bo5 de tournoi"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.run.user} vs {self.team} ({self.wins}-{self.losses})"

    @property
    def is_over(self) -> bool:
        return self.status in {self.Status.WON, self.Status.LOST}

    def used_champions(self) -> set[int]:
        """Fearless : tous les champions déjà joués dans ce Bo5, des deux côtés."""
        used = set()
        for game in self.games:
            used.update(int(c) for c in game["user_picks"].values())
            used.update(int(c) for c in game["opponent_picks"].values())
        return used


# --------------------------------------------------------------------- Quiz
class QuizAttempt(models.Model):
    """Le quiz d'un joueur du site sur une période de 4 h (cards/quiz.py).

    questions : la liste des 3 questions, réponses comprises (jamais
    envoyées telles quelles au navigateur) : {kind, text, options: [{id,
    label, sub}], answer: [ids], pick (nombre à choisir), attempts, picked,
    result}."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE, related_name="quizzes"
    )
    period_start = models.DateTimeField("période")
    questions = models.JSONField("questions", default=list)
    score = models.PositiveSmallIntegerField("bonnes réponses", default=0)
    finished_at = models.DateTimeField("terminé le", null=True, blank=True)
    reward_rarity = models.ForeignKey(Rarity, verbose_name="rareté gagnée", on_delete=models.PROTECT, null=True,
                                      blank=True, related_name="+")
    reward_choices = models.ManyToManyField(Card, verbose_name="cartes proposées", blank=True, related_name="+")
    chosen = models.ForeignKey(Card, verbose_name="carte choisie", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="+")
    created_at = models.DateTimeField("commencé le", auto_now_add=True)

    class Meta:
        verbose_name = "quiz"
        verbose_name_plural = "quiz"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "period_start"], name="one_quiz_per_period")]

    def __str__(self):
        return f"{self.user} — {self.period_start:%d/%m %H:%M} ({self.score}/3)"

    @property
    def current(self) -> int | None:
        """L'indice de la question en cours, None quand tout est répondu."""
        return next((i for i, q in enumerate(self.questions) if q.get("result") is None), None)


# --------------------------------------------------------------------- Méta de la semaine
class WeeklyMeta(models.Model):
    """La méta d'une semaine (du lundi 00:00 au lundi suivant) pour les
    équipes de la semaine : un bonus par poste, chacun d'un type différent
    tiré au sort (cards/weekly.py).

    rules : {rôle: {kind, value, label, bonus}}."""

    week = models.DateField("semaine (lundi)", unique=True)
    rules = models.JSONField("bonus par poste", default=dict)
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        verbose_name = "méta de la semaine"
        verbose_name_plural = "métas des semaines"
        ordering = ["-week"]

    def __str__(self):
        return f"Semaine du {self.week:%d/%m/%Y}"


class Supporter(models.Model):
    """L'équipe de cœur d'un joueur du site : +1 % au score de ses équipes
    (permanentes et de la semaine, pas en tournoi) par joueur de cette
    équipe aligné, toutes années confondues."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE, related_name="supporter"
    )
    team = models.ForeignKey(Team, verbose_name="équipe de cœur", on_delete=models.CASCADE, related_name="supporters")
    updated_at = models.DateTimeField("choisie le", auto_now=True)

    class Meta:
        verbose_name = "équipe de cœur"
        verbose_name_plural = "équipes de cœur"

    def __str__(self):
        return f"{self.user} ❤ {self.team}"


# --------------------------------------------------------------------- La Faille (clicker)
class FailleData(models.Model):
    """Les données de la Faille venues de l'extérieur (cards/faille.py) :
    « items » = le catalogue d'objets (Data Dragon), « images » = les
    chemins des images téléchargées, « awarded:<semaine> » = les succès de
    classement attribués."""

    key = models.CharField("clé", max_length=60, unique=True)
    version = models.CharField("version", max_length=30, blank=True)
    data = models.JSONField("données", default=dict)
    updated_at = models.DateTimeField("mis à jour", auto_now=True)

    class Meta:
        verbose_name = "donnée de la Faille"
        verbose_name_plural = "données de la Faille"

    def __str__(self):
        return f"{self.key} ({self.version})"


class FailleRun(models.Model):
    """La partie d'un joueur du site dans la Faille, pour une semaine.
    state : étape, or, inventaire, augments, drakes, charges… (cards/faille.py)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE, related_name="faille_runs"
    )
    week = models.DateField("semaine (lundi)")
    state = models.JSONField("état", default=dict)
    kills = models.PositiveIntegerField("ennemis vaincus", default=0, db_index=True)
    best = models.PositiveIntegerField("étape la plus profonde", default=0)
    synced_at = models.DateTimeField("dernière sauvegarde", null=True, blank=True)
    created_at = models.DateTimeField("commencée le", auto_now_add=True)

    class Meta:
        verbose_name = "partie de la Faille"
        verbose_name_plural = "parties de la Faille"
        ordering = ["-week", "-kills"]
        constraints = [models.UniqueConstraint(fields=["user", "week"], name="one_faille_run_per_week")]

    def __str__(self):
        return f"{self.user} — semaine du {self.week:%d/%m} ({self.kills} ennemis)"


class FailleAchievement(models.Model):
    """Un succès de la Faille, débloqué pour toujours (+0,05 % aux équipes)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="joueur du site", on_delete=models.CASCADE,
        related_name="faille_achievements",
    )
    code = models.CharField("succès", max_length=40)
    unlocked_at = models.DateTimeField("débloqué le", auto_now_add=True)

    class Meta:
        verbose_name = "succès de la Faille"
        verbose_name_plural = "succès de la Faille"
        ordering = ["-unlocked_at"]
        constraints = [models.UniqueConstraint(fields=["user", "code"], name="one_achievement_per_user")]

    def __str__(self):
        return f"{self.user} — {self.code}"

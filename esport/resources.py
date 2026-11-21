"""Écrans de gestion des données Leaguepedia (django-generic).

Chaque ``ModelResource`` génère la liste (filtres, recherche, exports),
la page de résumé, les formulaires et l'API. Les données arrivent par
l'import ; ces écrans servent à les vérifier et à les corriger.
"""

from django.db.models import BooleanField, Case, Count, Exists, Max, OuterRef, Value, When
from django.utils.translation import gettext_lazy as _

from generic.sites import (
    Cards,
    Kpi,
    ModelResource,
    RelatedTable,
    ResourcePage,
    TagStyle,
    action,
    display,
    register,
)

from .models import (
    Champion,
    GameAppearance,
    League,
    Player,
    PlayerImage,
    PlayerSplitStats,
    Split,
    Team,
    Tournament,
)
from .form_pages import ImportEverythingPage, ImportLeaguePage, RegroupSplitsPage, SyncChampionsPage, SyncLeaguesPage
from .tasks import download_images, import_league, import_splits, recompute_splits

GROUP = _("Données Leaguepedia")

ROLE_COLORS = {
    "Top": "#dc2626",
    "Jungle": "#16a34a",
    "Mid": "#2563eb",
    "Bot": "#d97706",
    "Support": "#9333ea",
}

TO_REVIEW = {"column": "needs_review", "operator": "is_true"}


def file_icon(resource, obj, field, label):
    """Une icône vers l'image publique (ouverte dans un onglet), ou rien.

    Photos et logos sont publics : leur adresse directe sous
    GAME_MEDIA_URL, sans passer par le téléchargement contrôlé du
    framework."""
    image = getattr(obj, field)

    if not image:
        return []

    return [
        {
            "icon": "image",
            "label": label,
            "url": image.url,
            "target": "_blank",
        }
    ]


@register(League)
class LeagueResource(ModelResource):
    actions = ("import_current_year", "remove_import", "delete_selected")

    icon = "emoji_events"
    group = GROUP
    order = 0
    description = _("Les ligues dont des tournois ont été importés.")

    list_display = (
        "short_name", "name", "region", "level", "prestige", "prestige_effective", "is_official", "imported",
        "split_count", "last_import",
    )
    editable_fields = ("prestige",)
    list_editable = True
    search_fields = ("short_name", "name", "region")
    ordering = ("short_name", "name")
    presets = {
        _("Ligues importées"): {
            "filters": {"match": "all", "conditions": [
                {"column": "imported", "operator": "is_true"}]},
        },
        _("Ligues officielles majeures"): {
            "filters": {"match": "all", "conditions": [
                {"column": "is_official", "operator": "is_true"},
                {"column": "level", "operator": "equals", "value": "Primary"}]},
        },
    }
    pages = (
        ResourcePage(
            "synchroniser",
            view=SyncLeaguesPage,
            title=_("Synchroniser les ligues"),
            icon="sync",
            permission="add",
            navigation=True,
        ),
    )
    related_tables = (
        RelatedTable("splits", icon="calendar_month"),
        RelatedTable("tournaments", icon="trophy"),
    )

    def get_list_queryset(self, request):
        return (
            super()
            .get_list_queryset(request)
            .annotate(
                splits_count=Count("splits", distinct=True),
                is_imported=Exists(Split.objects.filter(league=OuterRef("pk"))),
                last_imported=Max("splits__last_imported_at"),
            )
        )

    @display(
        description=_("Splits"),
        ordering="splits_count",
        filter_field="splits_count",
        filter_type="integer",
    )
    def split_count(self, league):
        return getattr(league, "splits_count", 0)

    @display(description=_("Prestige appliqué"))
    def prestige_effective(self, league):
        value = league.effective_prestige
        suffix = "" if league.prestige is not None else " (défaut)"
        return f"× {value:.2f}{suffix}"

    @display(
        description=_("Importée"),
        boolean=True,
        ordering="is_imported",
        filter_field="is_imported",
        filter_type="boolean",
    )
    def imported(self, league):
        if hasattr(league, "is_imported"):
            return league.is_imported
        return league.splits.exists()

    @display(
        description=_("Dernier import"),
        ordering="last_imported",
        filter_field="last_imported",
        filter_type="datetime",
    )
    def last_import(self, league):
        return getattr(league, "last_imported", None)

    @action(
        description=_("Importer l'année en cours"),
        icon="cloud_download",
        permissions=("change",),
        confirm=_("Importer les tournois de l'année en cours de ces ligues ?"),
        help=_(
            "Importe parties, joueurs, photos et cartes des ligues cochées "
            "pour l'année en cours. Pour d'autres années : Splits › "
            "Importer une ligue."
        ),
    )
    def import_current_year(self, request, queryset):
        from django.utils import timezone

        return import_league.start(
            request,
            background=True,
            leagues=sorted(queryset.values_list("name", flat=True)),
            years=[timezone.localdate().year],
        )

    @action(
        description=_("Retirer l'import"),
        icon="remove_circle",
        variant="danger",
        permissions=("delete",),
        confirm=_(
            "Retirer tout ce qui a été importé pour ces ligues ? Splits, "
            "parties, stats et cartes sont supprimés, y compris les "
            "exemplaires que les joueurs du site possèdent. La ligue reste "
            "dans la liste et pourra être réimportée."
        ),
        help=_(
            "Supprime les données importées des ligues cochées. Les "
            "boosters automatiques vidés sont supprimés, ceux faits à la "
            "main qui perdent leur filtre sont désactivés."
        ),
    )
    def remove_import(self, request, queryset):
        from cards.services import describe_removal, remove_league_data

        messages = [describe_removal(str(league), remove_league_data(league)) for league in queryset]
        return {"level": "success", "message": " ".join(messages) or _("Rien à retirer.")}

    def delete_model(self, request, obj):
        """Supprimer une ligue nettoie aussi boosters et joueurs orphelins."""
        from cards.services import remove_league_data

        remove_league_data(obj)
        super().delete_model(request, obj)


@register(Split)
class SplitResource(ModelResource):
    icon = "calendar_month"
    group = GROUP
    order = 1
    description = _(
        "Une période du jeu : toute l'année d'une ligue locale (Cup, "
        "splits, playoffs ensemble), ou une compétition internationale. "
        "Une carte = un joueur × une période."
    )

    list_display = (
        "name",
        "league",
        "year",
        "start_date",
        "end_date",
        "is_playable",
        "tournament_count",
        "card_count",
        "last_imported_at",
    )
    search_fields = ("name", "league__short_name", "league__name")
    ordering = ("-year", "-start_date", "name")
    editable_fields = ("is_playable",)
    list_editable = "toggle"
    actions = (
        "import_from_leaguepedia",
        "fetch_images",
        "recompute",
        "delete_selected",
    )
    # Le point de départ : une ligue et une année, tout le reste suit.
    pages = (
        ResourcePage(
            "importer-une-ligue",
            view=ImportLeaguePage,
            title=_("Importer une ligue"),
            icon="travel_explore",
            permission="add",
            navigation=True,
        ),
        ResourcePage(
            "tout-importer",
            view=ImportEverythingPage,
            title=_("Importer tout le jeu"),
            icon="cloud_sync",
            permission="add",
            navigation=True,
        ),
        ResourcePage(
            "regrouper",
            view=RegroupSplitsPage,
            title=_("Regrouper les phases par année"),
            icon="merge",
            permission="change",
            navigation=True,
        ),
    )

    fieldsets = (
        (None, {"fields": (("name", "slug"), ("league", "year"))}),
        (
            _("Dates"),
            {"fields": (("split_label", "start_date", "end_date"),)},
        ),
        (_("Jeu"), {"fields": ("is_playable",)}),
    )
    readonly_fields = ("last_imported_at",)
    detail_stats = ("tournament_count", "card_count", "player_count")
    related_tables = (
        RelatedTable("tournaments", icon="trophy"),
        RelatedTable("cards", icon="playing_cards"),
        RelatedTable("player_stats", icon="query_stats"),
    )

    def get_list_queryset(self, request):
        return (
            super()
            .get_list_queryset(request)
            .annotate(
                tournaments_count=Count("tournaments", distinct=True),
                cards_count=Count("cards", distinct=True),
            )
        )

    @display(
        description=_("Tournois"),
        ordering="tournaments_count",
        filter_field="tournaments_count",
        filter_type="integer",
    )
    def tournament_count(self, split):
        if hasattr(split, "tournaments_count"):
            return split.tournaments_count
        return split.tournaments.count()

    @display(
        description=_("Cartes"),
        ordering="cards_count",
        filter_field="cards_count",
        filter_type="integer",
    )
    def card_count(self, split):
        if hasattr(split, "cards_count"):
            return split.cards_count
        return split.cards.count()

    @display(description=_("Joueurs"))
    def player_count(self, split):
        return split.player_stats.count()

    @action(
        description=_("Importer depuis Leaguepedia"),
        icon="cloud_download",
        permissions=("change",),
        confirm=_(
            "Importer les parties, joueurs et photos des splits choisis ? "
            "Cela prend quelques minutes par split."
        ),
        help=_(
            "Récupère tout depuis Leaguepedia en tâche de fond, puis "
            "génère les cartes. Un rapport s'affiche à la fin."
        ),
    )
    def import_from_leaguepedia(self, request, queryset):
        return import_splits.start(
            request, ids=list(queryset.values_list("pk", flat=True))
        )

    @action(
        description=_("Télécharger les photos manquantes"),
        icon="image",
        permissions=("change",),
        help=_(
            "Retente le téléchargement des photos et logos absents, sans "
            "réimporter les parties."
        ),
    )
    def fetch_images(self, request, queryset):
        return download_images.start(
            request, ids=list(queryset.values_list("pk", flat=True))
        )

    @action(
        description=_("Recalculer stats et cartes"),
        icon="calculate",
        permissions=("change",),
        help=_(
            "Recalcule notes, champions et équipes depuis les données "
            "locales, sans appel à Leaguepedia."
        ),
    )
    def recompute(self, request, queryset):
        return recompute_splits.start(
            request, ids=list(queryset.values_list("pk", flat=True))
        )


@register(Tournament)
class TournamentResource(ModelResource):
    icon = "trophy"
    group = GROUP
    order = 2
    description = _("Les tournois Leaguepedia et le split auquel ils comptent.")

    list_display = (
        "name",
        "league",
        "split",
        "start_date",
        "end_date",
        "is_playoffs",
        "is_qualifier",
        "games_imported",
        "last_imported_at",
    )
    search_fields = ("name", "overview_page")
    ordering = ("-start_date", "name")
    # Le rattachement à un split se corrige directement dans la liste.
    editable_fields = ("split",)
    list_editable = "toggle"
    readonly_fields = ("games_imported", "last_imported_at")
    related_tables = (RelatedTable("appearances", icon="sports_esports"),)


@register(Team)
class TeamResource(ModelResource):
    icon = "groups"
    group = GROUP
    order = 3
    description = _("Les équipes rencontrées dans les scoreboards.")

    list_display = ("logo_link", "name", "short_name", "card_count")
    search_fields = ("name", "short_name")
    fields = (("name", "short_name"), "logo_filename", "logo")
    related_tables = (
        RelatedTable("cards", icon="playing_cards"),
        RelatedTable("split_stats", icon="query_stats"),
    )

    def get_list_queryset(self, request):
        return (
            super()
            .get_list_queryset(request)
            .annotate(cards_count=Count("cards", distinct=True))
        )

    @display(description=_("Logo"), icons=True, align="center")
    def logo_link(self, team):
        return file_icon(self, team, "logo", _("Voir le logo"))

    @display(
        description=_("Cartes"),
        ordering="cards_count",
        filter_field="cards_count",
        filter_type="integer",
    )
    def card_count(self, team):
        return getattr(team, "cards_count", 0)


@register(Player)
class PlayerResource(ModelResource):
    icon = "person"
    group = GROUP
    order = 4
    description = _(
        "Les joueurs pros. « À vérifier » : pas trouvés automatiquement "
        "sur Leaguepedia."
    )

    list_display = (
        "photo_link",
        "ign",
        "real_name",
        "birthdate",
        "country",
        "role",
        "current_team",
        "is_retired",
        "needs_review",
        "card_count",
        "has_photo",
    )
    list_display_hidden = ("is_retired", "has_photo")
    search_fields = ("ign", "real_name", "native_name", "overview_page")
    tag_fields = {"role": TagStyle(colors=ROLE_COLORS)}
    editable_fields = ("real_name", "birthdate", "country", "needs_review")
    list_editable = "toggle"
    presets = {
        _("À vérifier"): {
            "filters": {"match": "all", "conditions": [TO_REVIEW]},
        },
        _("Sans photo"): {
            "filters": {
                "match": "all",
                "conditions": [{"column": "has_photo", "operator": "is_false"}],
            },
        },
    }

    fieldsets = (
        (None, {"fields": (("ign", "overview_page"), ("role", "current_team"))}),
        (
            _("Identité"),
            {
                "fields": (
                    ("real_name", "native_name"),
                    ("birthdate", "country"),
                    "nationality",
                )
            },
        ),
        (_("Photo"), {"fields": ("image_filename", "photo")}),
        (_("Contrôle"), {"fields": (("needs_review", "is_retired"),)}),
    )
    readonly_fields = ("last_synced_at",)
    detail_stats = ("card_count", "birth_year")
    detail_fieldsets = (
        (
            None,
            {
                "fields": (
                    "ign",
                    "real_name",
                    "native_name",
                    "birthdate",
                    "country",
                    "nationality",
                    "role",
                    "current_team",
                    "photo",
                    "needs_review",
                    "last_synced_at",
                )
            },
        ),
    )
    related_tables = (
        RelatedTable("cards", icon="playing_cards"),
        RelatedTable("split_stats", icon="query_stats"),
        RelatedTable("images", icon="photo_library"),
        RelatedTable("appearances", icon="sports_esports"),
    )
    kpis = (
        Kpi(
            "to_review",
            title=_("Joueurs à vérifier"),
            icon="person_search",
            filters={"match": "all", "conditions": [TO_REVIEW]},
            warning=1,
            danger=20,
        ),
    )
    cards = (
        Cards(
            "to_review",
            title=_("Joueurs à vérifier"),
            filters={"match": "all", "conditions": [TO_REVIEW]},
            ordering=("ign",),
            subtitle="current_team",
            fields=("role",),
            image="photo",
            limit=6,
        ),
    )

    def get_list_queryset(self, request):
        return (
            super()
            .get_list_queryset(request)
            .annotate(
                cards_count=Count("cards", distinct=True),
                photo_present=Case(
                    When(photo="", then=Value(False)),
                    default=Value(True),
                    output_field=BooleanField(),
                ),
            )
        )

    @display(description=_("Photo"), icons=True, align="center")
    def photo_link(self, player):
        return file_icon(self, player, "photo", _("Voir la photo"))

    @display(
        description=_("Photo présente"),
        boolean=True,
        ordering="photo_present",
        filter_field="photo_present",
        filter_type="boolean",
    )
    def has_photo(self, player):
        return bool(player.photo)

    @display(
        description=_("Cartes"),
        ordering="cards_count",
        filter_field="cards_count",
        filter_type="integer",
    )
    def card_count(self, player):
        if hasattr(player, "cards_count"):
            return player.cards_count
        return player.cards.count()

    @display(description=_("Année de naissance"))
    def birth_year(self, player):
        return player.birth_year or "-"


@register(PlayerImage)
class PlayerImageResource(ModelResource):
    icon = "photo_library"
    group = GROUP
    order = 5
    show_in_navigation = False

    list_display = (
        "file_link",
        "filename",
        "player",
        "tournament_name",
        "team_name",
        "image_type",
    )
    search_fields = ("filename", "player__ign", "tournament_name")
    fields = ("player", "filename", ("tournament_name", "team_name"), "file")

    @display(description=_("Image"), icons=True, align="center")
    def file_link(self, image):
        return file_icon(self, image, "file", _("Voir l'image"))


@register(GameAppearance)
class GameAppearanceResource(ModelResource):
    """Les lignes de scoreboard : en lecture, pour comprendre une stat."""

    icon = "sports_esports"
    group = GROUP
    order = 6
    label_plural = _("parties jouées")
    description = _("Une ligne par joueur et par partie importée.")
    # Des dizaines de milliers de lignes réécrites à chaque import :
    # ni historique ni temps réel.
    history = False
    realtime = False

    list_display = (
        "played_at",
        "player",
        "team",
        "opponent",
        "role",
        "champion",
        "kills",
        "deaths",
        "assists",
        "cs",
        "damage",
        "win",
        "rating",
        "tournament",
    )
    search_fields = ("player__ign", "champion", "game_id", "opponent")
    tag_fields = {"role": TagStyle(colors=ROLE_COLORS)}
    ordering = ("-played_at", "game_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@register(PlayerSplitStats)
class PlayerSplitStatsResource(ModelResource):
    icon = "query_stats"
    group = GROUP
    order = 7
    label_plural = _("stats par split")
    description = _(
        "Les agrégats d'un joueur sur un split et sa note : le percentile "
        "donne la rareté de sa carte."
    )
    history = False
    realtime = False

    list_display = (
        "player",
        "split",
        "team",
        "role",
        "games",
        "win_rate",
        "kda",
        "kill_participation",
        "cs_per_min",
        "gold_per_min",
        "damage_per_min",
        "vision_per_min",
        "damage_share",
        "score",
        "percentile",
    )
    search_fields = ("player__ign", "team__name")
    tag_fields = {"role": TagStyle(colors=ROLE_COLORS)}
    ordering = ("split", "-score")
    exclude = ("champions", "dimensions")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@register(Champion)
class ChampionResource(ModelResource):
    icon = "shield_person"
    group = GROUP
    order = 8
    description = _(
        "Les champions : ceux vus dans les parties importées, complétés par "
        "Data Dragon (titre, classes, profil, images) avec « Synchroniser "
        "les champions ». L'identifiant Riot relie les deux : corrige-le si "
        "un champion n'a pas d'image."
    )

    list_display = ("icon_link", "name", "title", "key", "tags", "attack", "defense", "magic", "card_count",
                    "synced_at")
    search_fields = ("name", "key", "title")
    editable_fields = ("key",)
    list_editable = True
    fieldsets = (
        (None, {"fields": (("name", "key"), "title")}),
        (_("Profil"), {"fields": ("tags", ("attack", "defense", "magic", "difficulty"))}),
        (_("Images"), {"fields": (("icon", "portrait", "splash"),)}),
    )
    related_tables = (RelatedTable("card_affinities", icon="playing_cards"),)
    pages = (
        ResourcePage(
            "synchroniser",
            view=SyncChampionsPage,
            title=_("Synchroniser les champions"),
            icon="sync",
            permission="change",
            navigation=True,
        ),
    )
    presets = {
        _("Sans Data Dragon"): {
            "filters": {"match": "all", "conditions": [{"column": "synced_at", "operator": "is_empty"}]},
        },
    }

    def get_list_queryset(self, request):
        return (
            super()
            .get_list_queryset(request)
            .annotate(cards_count=Count("card_affinities", distinct=True))
        )

    @display(description=_("Image"), icons=True, align="center")
    def icon_link(self, champion):
        if not champion.key:
            return []
        return [{"icon": "image", "label": _("Voir l'illustration"), "url": champion.portrait_url,
                 "target": "_blank"}]

    @display(
        description=_("Cartes"),
        ordering="cards_count",
        filter_field="cards_count",
        filter_type="integer",
    )
    def card_count(self, champion):
        if hasattr(champion, "cards_count"):
            return champion.cards_count
        return champion.card_affinities.count()

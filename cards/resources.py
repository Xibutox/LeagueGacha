"""Écrans de gestion du jeu (django-generic) : raretés, cartes, boosters,
et ce que les joueurs du site ont tiré."""

from decimal import Decimal

from django.db.models import Avg, Case, Count, DecimalField, IntegerField, Sum, Value, When
from django.utils.translation import gettext_lazy as _

from esport.models import Split
from esport.resources import ROLE_COLORS, file_icon
from esport.tasks import recompute_splits
from generic.sites import (
    Chart,
    Kpi,
    ModelResource,
    RelatedTable,
    ResourcePage,
    TabularInline,
    TagStyle,
    action,
    display,
    page,
    register,
)

from .form_pages import GiveBoostersPage, InitBoostersPage, ResetProgressPage
from .models import (
    ExchangeOffer,
    QuizAttempt,
    Duel,
    FailleAchievement,
    FailleRun,
    Supporter,
    WeeklyMeta,
    Tournament,
    TournamentSeries,
    Portal,
    Booster,
    BoosterGift,
    BoosterRarityWeight,
    BoosterStock,
    Card,
    PackOpening,
    PackOpeningCard,
    Rarity,
    CardChampion,
    Roster,
    RosterPick,
    RosterSlot,
    UserCard,
)

GAME = _("Jeu")
PLAYERS = _("Joueurs du site")

RARITY_TAG = TagStyle(color="color")


@register(Rarity)
class RarityResource(ModelResource):
    icon = "diamond"
    group = GAME
    order = 0
    description = _(
        "Chaque joueur existe à toutes les raretés. Taux de tirage : la "
        "chance qu'un emplacement de booster soit de cette rareté (ramenés "
        "à 100 % si le total n'y fait pas, voir « Taux effectif »). Bonus : "
        "ajouté au score d'un poste d'équipe. Fusion : combien "
        "d'exemplaires donnent la rareté suivante. Échange : les doublons "
        "à donner pour choisir n'importe quel joueur à la rareté d'échange."
    )

    list_display = (
        "badge",
        "code",
        "order",
        "drop_rate",
        "effective_rate",
        "score_bonus",
        "fusion_cost",
        "exchange_cost",
        "exchange_rarity",
        "copy_count",
    )
    search_fields = ("name", "code")
    ordering = ("order",)
    tag_fields = {"exchange_rarity": RARITY_TAG}
    editable_fields = ("order", "drop_rate", "score_bonus", "fusion_cost", "exchange_cost", "exchange_rarity")
    list_editable = True
    fieldsets = (
        (None, {"fields": (("name", "code"), ("order", "color"))}),
        (_("Boosters"), {"fields": ("drop_rate",)}),
        (_("Équipes"), {"fields": ("score_bonus",)}),
        (_("Doublons"), {"fields": ("fusion_cost", ("exchange_cost", "exchange_rarity"))}),
    )
    form_overrides = {"color": {"widget": "color"}}
    related_tables = (RelatedTable("copies", icon="collections_bookmark"),)
    kpis = (
        Kpi(
            "total_rate",
            title=_("Total des taux de tirage"),
            icon="percent",
            value=Sum("drop_rate"),
            unit="%",
            decimals=2,
            description=_("Doit faire 100 %."),
        ),
    )

    def get_list_queryset(self, request):
        # Le total des taux : une requête par page, pas une par ligne.
        total = Rarity.objects.aggregate(total=Sum("drop_rate"))["total"]
        return (
            super()
            .get_list_queryset(request)
            .annotate(
                copies_count=Sum("copies__quantity"),
                total_rate=Value(
                    total or Decimal("0"),
                    output_field=DecimalField(max_digits=7, decimal_places=2),
                ),
            )
        )

    @display(description=_("Rareté"), tags=True, ordering="order")
    def badge(self, rarity):
        return [{"label": rarity.name, "color": rarity.color}]

    @display(description=_("Taux effectif"))
    def effective_rate(self, rarity):
        total = getattr(rarity, "total_rate", None)
        if total is None:
            total = Rarity.objects.aggregate(total=Sum("drop_rate"))["total"]
        if not total:
            return "-"
        return f"{100 * rarity.drop_rate / total:.2f} %"

    @display(
        description=_("Exemplaires possédés"),
        ordering="copies_count",
        filter_field="copies_count",
        filter_type="integer",
    )
    def copy_count(self, rarity):
        if hasattr(rarity, "copies_count"):
            return rarity.copies_count or 0
        return rarity.copies.aggregate(n=Sum("quantity"))["n"] or 0


@register(Card)
class CardResource(ModelResource):
    icon = "playing_cards"
    group = GAME
    order = 1
    description = _(
        "Une carte par joueur et par période (l'année d'une ligue, ou une "
        "compétition internationale). La rareté est celle des exemplaires "
        "possédés. La photo est choisie à l'import ; verrouille-la pour "
        "garder une correction."
    )

    list_display = (
        "photo_link",
        "player",
        "split",
        "team",
        "role",
        "overall",
        "image",
        "image_locked",
        "is_active",
        "owner_count",
    )
    list_display_hidden = ("image", "image_locked")
    search_fields = ("player__ign", "player__real_name", "team__name", "split__name")
    tag_fields = {"role": TagStyle(colors=ROLE_COLORS)}
    ordering = ("split", "-overall", "player__ign")
    editable_fields = ("is_active",)
    list_editable = "toggle"
    bulk_edit_fields = ("is_active",)
    actions = ("recompute", "delete_selected")

    fieldsets = (
        (None, {"fields": (("player", "split"), ("team", "role"), "overall")}),
        (_("Photo"), {"fields": (("image", "image_locked"),)}),
        (_("Jeu"), {"fields": ("is_active",)}),
    )
    readonly_fields = ("overall",)
    detail_stats = ("overall", "owner_count", "stats_line")
    related_tables = (
        RelatedTable("champions", icon="shield_person"),
        RelatedTable("owners", icon="person"),
    )

    charts = (
        Chart(
            "by_role",
            title=_("Par rôle"),
            type="donut",
            group_by="role",
        ),
        Chart(
            "by_split",
            title=_("Par période"),
            type="bar",
            group_by="split",
            split_by="role",
            stacked=True,
        ),
    )
    list_charts = ("by_role", "by_split")
    kpis = (
        Kpi("active", title=_("Cartes jouables"), icon="playing_cards",
            filters={"match": "all", "conditions": [
                {"column": "is_active", "operator": "is_true"}]}),
    )

    def get_list_queryset(self, request):
        return (
            super()
            .get_list_queryset(request)
            .select_related("player", "image", "split", "team")
            .annotate(owners_count=Count("owners", distinct=True))
        )

    @display(description=_("Photo"), icons=True, align="center")
    def photo_link(self, card):
        if card.image_id and card.image.file:
            return [
                {
                    "icon": "image",
                    "label": _("Voir la photo"),
                    "url": card.image.file.url,
                    "target": "_blank",
                }
            ]
        if card.player.photo:
            return [
                {
                    "icon": "account_box",
                    "label": _("Photo principale du joueur"),
                    "url": card.player.photo.url,
                    "target": "_blank",
                }
            ]
        return []

    @display(
        description=_("Collectionneurs"),
        ordering="owners_count",
        filter_field="owners_count",
        filter_type="integer",
    )
    def owner_count(self, card):
        if hasattr(card, "owners_count"):
            return card.owners_count
        return card.owners.count()

    @display(description=_("Stats"))
    def stats_line(self, card):
        s = card.stats
        if s is None:
            return "-"
        return (
            f"{s.games} parties, {s.win_rate:g} % V, KDA {s.kda:g}, "
            f"DPM {s.damage_per_min if s.damage_per_min is not None else '—'}, percentile {s.percentile:g}"
        )

    @action(
        description=_("Recalculer les notes"),
        icon="calculate",
        permissions=("change",),
        help=_(
            "Recalcule les périodes des cartes choisies : notes, champions "
            "et équipes (les photos verrouillées sont gardées)."
        ),
    )
    def recompute(self, request, queryset):
        split_ids = list(
            Split.objects.filter(cards__in=queryset)
            .distinct()
            .values_list("pk", flat=True)
        )
        return recompute_splits.start(request, ids=split_ids)

    @page(
        title=_("Aperçu"),
        detail=True,
        icon="visibility",
        row_menu=True,
        template="cards/gestion/card_preview.html",
    )
    def apercu(self, request, card):
        """La carte telle que les joueurs la voient, à chaque rareté."""
        return {"card": card, "rarities": Rarity.objects.all()}


class BoosterRarityWeightInline(TabularInline):
    model = BoosterRarityWeight
    fields = ("rarity", "rate")
    extra = 0


@register(Booster)
class BoosterResource(ModelResource):
    icon = "redeem"
    group = GAME
    order = 2
    description = _(
        "Ce que les joueurs peuvent ouvrir. Un booster pioche dans les "
        "cartes des périodes jouables qui passent tous ses filtres (ligues, "
        "années, splits) ; un filtre vide ne filtre pas. L'import crée un "
        "booster par région et par année. Chaque joueur reçoit un booster "
        "toutes les N heures, jusqu'au stock maximum ; au-delà, ils sont "
        "perdus."
    )

    list_display = (
        "visual",
        "name",
        "content",
        "auto",
        "cards_per_pack",
        "guaranteed_rarity",
        "cooldown_hours",
        "max_stock",
        "pool_size",
        "opening_count",
        "is_active",
        "order",
    )
    search_fields = ("name", "slug")
    tag_fields = {"guaranteed_rarity": RARITY_TAG}
    editable_fields = ("is_active", "order", "cooldown_hours", "max_stock")
    list_editable = "toggle"
    fieldsets = (
        (None, {"fields": (("name", "slug"), "description")}),
        (
            _("Contenu"),
            {
                "fields": (
                    "leagues",
                    ("years", "splits"),
                    ("cards_per_pack", "guaranteed_rarity"),
                )
            },
        ),
        (_("Rythme"), {"fields": (("cooldown_hours", "max_stock", "starting_stock"),)}),
        (_("Affichage"), {"fields": ("image", ("color", "order", "is_active"))}),
    )
    form_overrides = {"color": {"widget": "color"}}
    inlines = (BoosterRarityWeightInline,)
    related_tables = (RelatedTable("openings", icon="history"), RelatedTable("gifts", icon="redeem"))
    pages = (
        ResourcePage(
            "offrir",
            view=GiveBoostersPage,
            title=_("Offrir des boosters"),
            icon="featured_seasonal_and_gifts",
            permission="change",
            navigation=True,
        ),
        ResourcePage(
            "boosters-types",
            view=InitBoostersPage,
            title=_("Créer les boosters types"),
            icon="auto_awesome",
            permission="add",
            navigation=True,
        ),
    )

    def get_list_queryset(self, request):
        # La taille du pool de chaque booster, calculée en mémoire en un
        # nombre fixe de requêtes (les cartes jouables, les boosters et
        # leurs filtres), puis posée sur chaque ligne : pas de requête par
        # ligne.
        cards = list(
            Card.objects.filter(is_active=True, split__is_playable=True).values_list(
                "split_id", "split__league_id", "split__year"
            )
        )
        sizes = []
        for booster in Booster.objects.prefetch_related("leagues", "splits"):
            leagues = {l.pk for l in booster.leagues.all()}
            splits = {s.pk for s in booster.splits.all()}
            years = set(booster.year_list())
            size = sum(
                1
                for split_id, league_id, year in cards
                if (not leagues or league_id in leagues)
                and (not splits or split_id in splits)
                and (not years or year in years)
            )
            sizes.append(When(pk=booster.pk, then=Value(size)))
        return (
            super()
            .get_list_queryset(request)
            .prefetch_related("leagues", "splits")
            .annotate(
                openings_count=Count("openings", distinct=True),
                pool=Case(*sizes, default=Value(0), output_field=IntegerField()),
            )
        )

    @display(description=_("Visuel"), icons=True, align="center")
    def visual(self, booster):
        if not booster.image:
            return []
        return [{"icon": "image", "label": _("Voir le visuel"), "url": booster.image.url, "target": "_blank"}]

    @display(description=_("Contenu"))
    def content(self, booster):
        parts = [", ".join(l.short_name or l.name for l in booster.leagues.all())]
        parts.append(booster.years)
        parts.append(", ".join(s.name for s in booster.splits.all()))
        return " · ".join(p for p in parts if p) or _("Tout")

    @display(description=_("Cartes possibles"), ordering="pool")
    def pool_size(self, booster):
        if hasattr(booster, "pool"):
            return booster.pool
        return booster.card_pool().count()

    @display(
        description=_("Ouvertures"),
        ordering="openings_count",
        filter_field="openings_count",
        filter_type="integer",
    )
    def opening_count(self, booster):
        if hasattr(booster, "openings_count"):
            return booster.openings_count
        return booster.openings.count()


@register(BoosterRarityWeight)
class BoosterRarityWeightResource(ModelResource):
    icon = "balance"
    group = GAME
    show_in_navigation = False
    list_display = ("booster", "rarity", "rate")


@register(UserCard)
class UserCardResource(ModelResource):
    icon = "collections_bookmark"
    group = PLAYERS
    order = 0
    label_plural = _("collections")
    description = _("Les cartes que possède chaque joueur du site.")
    history = False

    list_display = (
        "user",
        "card",
        "rarity",
        "card__split",
        "quantity",
        "first_obtained_at",
        "last_obtained_at",
    )
    search_fields = ("user__username", "card__player__ign")
    tag_fields = {"rarity": RARITY_TAG}
    pages = (
        ResourcePage(
            "reinitialiser",
            view=ResetProgressPage,
            title=_("Réinitialiser des joueurs"),
            icon="restart_alt",
            permission="delete",
            navigation=True,
        ),
    )
    ordering = ("-last_obtained_at",)
    list_select_related = ("user", "card__player", "card__split", "rarity")
    fields = (("user", "card"), ("rarity", "quantity"))


@register(PackOpening)
class PackOpeningResource(ModelResource):
    icon = "history"
    group = PLAYERS
    order = 1
    description = _("Chaque booster ouvert, et ce qu'il contenait.")
    history = False

    list_display = ("opened_at", "user", "booster", "card_count")
    search_fields = ("user__username", "booster__name")
    ordering = ("-opened_at",)
    related_tables = (RelatedTable("cards", icon="playing_cards"),)
    charts = (
        Chart(
            "per_day",
            title=_("Ouvertures par jour"),
            group_by="opened_at",
            period="day",
            periods=("day", "week", "month"),
        ),
    )
    list_charts = ("per_day",)

    def get_list_queryset(self, request):
        return (
            super()
            .get_list_queryset(request)
            .annotate(cards_count=Count("cards", distinct=True))
        )

    @display(description=_("Cartes"), ordering="cards_count")
    def card_count(self, opening):
        if hasattr(opening, "cards_count"):
            return opening.cards_count
        return opening.cards.count()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@register(PackOpeningCard)
class PackOpeningCardResource(ModelResource):
    icon = "style"
    group = PLAYERS
    show_in_navigation = False
    history = False
    label_plural = _("cartes tirées")

    list_display = ("opening", "slot", "card", "rarity", "was_new")
    list_select_related = (
        "opening__user",
        "opening__booster",
        "card__player",
        "card__split",
        "rarity",
    )
    search_fields = ("card__player__ign", "opening__user__username")
    tag_fields = {"rarity": RARITY_TAG}

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False



class RosterSlotInline(TabularInline):
    model = RosterSlot
    fields = ("role", "card", "score")
    readonly_fields = ("score",)
    extra = 0


@register(Roster)
class RosterResource(ModelResource):
    icon = "groups_3"
    group = PLAYERS
    order = 2
    description = _("Les équipes de 5 montées par les joueurs du site.")

    list_display = ("name", "user", "week", "score", "slot_count", "overall_display", "updated_at")
    list_filter = ("week",)
    search_fields = ("name", "user__username")
    ordering = ("-score", "-updated_at")
    list_select_related = ("user",)
    fields = (("name", "user"), ("week", "score"))
    readonly_fields = ("score", "week")
    inlines = (RosterSlotInline,)
    related_tables = (RelatedTable("slots", icon="person"),)

    def get_list_queryset(self, request):
        return (
            super()
            .get_list_queryset(request)
            .annotate(slots_count=Count("slots", distinct=True), overall_avg=Avg("slots__card__overall"))
        )

    @display(description=_("Postes remplis"), ordering="slots_count")
    def slot_count(self, roster):
        n = getattr(roster, "slots_count", None)
        return f"{roster.slots.count() if n is None else n} / 5"

    @display(description=_("Note"), ordering="overall_avg")
    def overall_display(self, roster):
        avg = getattr(roster, "overall_avg", None)
        if avg is None:
            avg = roster.overall
        return round(avg) if avg is not None else "-"


@register(RosterSlot)
class RosterSlotResource(ModelResource):
    icon = "person"
    group = PLAYERS
    show_in_navigation = False
    list_display = ("roster", "role", "card", "score")
    list_select_related = ("roster__user", "card__player", "card__split")
    search_fields = ("roster__name", "card__player__ign")
    related_tables = (RelatedTable("picks", icon="shield_person"),)


@register(RosterPick)
class RosterPickResource(ModelResource):
    icon = "shield_person"
    group = PLAYERS
    show_in_navigation = False
    history = False
    list_display = ("slot", "composition", "champion", "score")
    list_select_related = ("slot__roster__user", "slot__card__player", "slot__card__split", "champion")
    search_fields = ("slot__roster__name", "champion__name")


@register(BoosterStock)
class BoosterStockResource(ModelResource):
    icon = "inventory_2"
    group = PLAYERS
    order = 3
    label_plural = _("réserves de boosters")
    description = _(
        "La réserve comptée au dernier passage de chaque joueur ; elle "
        "grossit d'un booster par période jusqu'au maximum."
    )
    history = False
    list_display = ("user", "booster", "stock", "bonus", "available", "refilled_at")
    list_select_related = ("user", "booster")
    search_fields = ("user__username", "booster__name")
    fields = (("user", "booster"), ("stock", "bonus"), "refilled_at")

    @display(description=_("Disponibles maintenant"))
    def available(self, stock):
        from .services import available_packs

        return available_packs(stock)[0]


@register(CardChampion)
class CardChampionResource(ModelResource):
    """Les champions joués par chaque carte : calculés à l'import."""

    icon = "shield_person"
    group = GAME
    order = 3
    label_plural = _("champions des cartes")
    history = False
    realtime = False
    show_in_navigation = False

    list_display = ("card", "champion", "games", "wins", "history_games", "kda", "rating")
    search_fields = ("card__player__ign", "champion__name")
    list_select_related = ("card__player", "card__split", "champion")
    ordering = ("card", "-games")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@register(BoosterGift)
class BoosterGiftResource(ModelResource):
    icon = "redeem"
    group = PLAYERS
    order = 4
    label_plural = _("cadeaux de boosters")
    description = _("Les boosters offerts par l'administration (Jeu › Boosters › Offrir des boosters).")
    history = False

    list_display = ("created_at", "user", "booster", "quantity", "given_by", "message", "seen_at")
    list_select_related = ("user", "booster", "given_by")
    search_fields = ("user__username", "booster__name", "message")
    ordering = ("-created_at",)
    fields = (("user", "booster"), ("quantity", "given_by"), "message", "seen_at")
    readonly_fields = ("given_by", "seen_at")

    def has_add_permission(self, request):
        return False


@register(Portal)
class PortalResource(ModelResource):
    icon = "auto_awesome_motion"
    group = GAME
    order = 3
    label_plural = _("portails")
    description = _(
        "Un booster ouvrable pendant un temps limité, hors réserve : chaque joueur "
        "y ouvre quelques boosters (le premier tout de suite, les suivants après "
        "une attente). Le portail du jour (24 h) se crée tout seul et tourne entre "
        "les boosters inactifs ; crée ici des portails de 1 à 30 jours."
    )

    list_display = ("title", "booster", "starts_at", "ends_at", "max_openings", "wait_hours", "auto", "is_active")
    list_select_related = ("booster",)
    search_fields = ("name", "booster__name")
    list_filter = ("auto", "is_active")
    ordering = ("-starts_at",)
    fields = (("name", "booster"), ("starts_at", "ends_at"), ("max_openings", "wait_hours"), "is_active")

    @display(description=_("Portail"))
    def title(self, obj):
        return obj.title


@register(ExchangeOffer)
class ExchangeOfferResource(ModelResource):
    icon = "swap_horiz"
    group = PLAYERS
    order = 5
    label_plural = _("échanges de doublons")
    description = _("Les doublons échangés et les 5 cartes proposées à chaque fois.")
    history = False

    list_display = ("created_at", "user", "given_card", "given_rarity", "rarity", "chosen", "chosen_at")
    list_select_related = (
        "user", "given_card__player", "given_card__split", "given_rarity", "rarity", "chosen__player", "chosen__split",
    )
    search_fields = ("user__username", "given_card__player__ign", "chosen__player__ign")
    ordering = ("-created_at",)
    fields = (("user", "created_at"), ("given_card", "given_rarity"), ("rarity", "chosen", "chosen_at"), "choices")
    readonly_fields = ("user", "created_at", "given_card", "given_rarity", "rarity", "chosen", "chosen_at", "choices")

    def has_add_permission(self, request):
        return False


@register(Tournament)
class TournamentResource(ModelResource):
    icon = "emoji_events"
    group = GAME
    order = 4
    label_plural = _("tournois")
    description = _(
        "Un tournoi international du passé, rejoué : les joueurs du site affrontent ses "
        "équipes de la dernière à la gagnante, en Bo5 avec draft Fearless. Un tournoi "
        "automatique de 7 jours se crée tout seul quand aucun n'est en cours ; crée-en "
        "d'autres ici, change leur durée ou l'attente entre deux Bo5."
    )

    list_display = ("title", "split", "starts_at", "ends_at", "cooldown_hours", "opponent_count", "auto", "is_active")
    list_select_related = ("split",)
    search_fields = ("name", "split__name")
    list_filter = ("auto", "is_active")
    ordering = ("-starts_at",)
    fields = (("name", "split"), ("starts_at", "ends_at"), ("cooldown_hours", "is_active"))

    @display(description=_("Tournoi"))
    def title(self, obj):
        return obj.title

    @display(description=_("Équipes"))
    def opponent_count(self, obj):
        return len(obj.opponents)

    def save_model(self, request, serializer, change):
        tournament = serializer.save()
        if not tournament.opponents and tournament.split_id:
            from esport.models import Team

            from .tournament import standings

            teams = standings(tournament.split)
            names = dict(Team.objects.filter(pk__in=teams).values_list("pk", "name"))
            tournament.opponents = [{"team": t, "name": names.get(t, "?")} for t in teams]
            tournament.save(update_fields=["opponents"])
        return tournament


@register(TournamentSeries)
class TournamentSeriesResource(ModelResource):
    icon = "sports_esports"
    group = PLAYERS
    order = 6
    label_plural = _("Bo5 de tournoi")
    description = _("Les Bo5 joués en mode tournoi.")
    history = False

    list_display = ("created_at", "run", "team", "status", "wins", "losses", "reward_card", "reward_rarity")
    list_select_related = ("run__user", "run__tournament__split", "team", "reward_card__player",
                           "reward_card__split", "reward_rarity")
    list_filter = ("status",)
    search_fields = ("run__user__username", "team__name")
    ordering = ("-created_at",)
    readonly_fields = ("run", "step", "team", "status", "opponent", "opponent_rarity", "lineup",
                       "lineup_rarities", "games", "wins", "losses", "reward_card", "reward_rarity", "finished_at")

    def has_add_permission(self, request):
        return False


@register(QuizAttempt)
class QuizAttemptResource(ModelResource):
    icon = "quiz"
    group = PLAYERS
    order = 7
    label_plural = _("quiz")
    description = _("Les quiz joués (un toutes les 4 h par joueur), leurs questions et les cartes gagnées.")
    history = False

    list_display = ("created_at", "user", "score", "reward_rarity", "chosen", "finished_at")
    list_select_related = ("user", "reward_rarity", "chosen__player", "chosen__split")
    search_fields = ("user__username",)
    ordering = ("-created_at",)
    readonly_fields = ("user", "period_start", "questions", "score", "finished_at", "reward_rarity",
                       "reward_choices", "chosen")

    def has_add_permission(self, request):
        return False


@register(WeeklyMeta)
class WeeklyMetaResource(ModelResource):
    icon = "event_repeat"
    group = GAME
    order = 20
    label_plural = _("métas de la semaine")
    description = _(
        "La méta de chaque semaine (équipes de la semaine) : un bonus par poste, tiré au sort le lundi. "
        "Supprimer celle de la semaine en cours la fait retirer au prochain affichage."
    )
    history = False

    list_display = ("week", "rules_display")
    ordering = ("-week",)
    readonly_fields = ("week", "rules")

    def has_add_permission(self, request):
        return False

    @display(description=_("Bonus"))
    def rules_display(self, meta):
        return " · ".join(f"{role} : {r.get('label')} (+{r.get('bonus')} %)" for role, r in (meta.rules or {}).items())


@register(Supporter)
class SupporterResource(ModelResource):
    icon = "favorite"
    group = PLAYERS
    order = 8
    label_plural = _("équipes de cœur")
    description = _("L'équipe de cœur de chaque joueur du site : +1 % par joueur de cette équipe aligné.")
    history = False

    list_display = ("user", "team", "updated_at")
    list_select_related = ("user", "team")
    search_fields = ("user__username", "team__name")
    ordering = ("user__username",)


@register(FailleRun)
class FailleRunResource(ModelResource):
    icon = "swords"
    group = PLAYERS
    order = 9
    label_plural = _("parties de la Faille")
    description = _("Les parties de la Faille (le clicker), une par joueur et par semaine.")
    history = False

    list_display = ("week", "user", "kills", "best", "synced_at")
    list_filter = ("week",)
    list_select_related = ("user",)
    search_fields = ("user__username",)
    ordering = ("-week", "-kills")
    readonly_fields = ("user", "week", "state", "kills", "best", "synced_at")

    def has_add_permission(self, request):
        return False


@register(FailleAchievement)
class FailleAchievementResource(ModelResource):
    icon = "military_tech"
    group = PLAYERS
    order = 10
    label_plural = _("succès de la Faille")
    description = _("Les succès débloqués dans la Faille : chacun ajoute 0,05 % au score des équipes.")
    history = False

    list_display = ("user", "code", "unlocked_at")
    list_select_related = ("user",)
    search_fields = ("user__username", "code")
    ordering = ("-unlocked_at",)

    def has_add_permission(self, request):
        return False


@register(Duel)
class DuelResource(ModelResource):
    icon = "sports_kabaddi"
    group = PLAYERS
    order = 11
    label_plural = _("duels 1V1")
    description = _("Les Bo5 entre joueurs du site : invitations, duels en cours et terminés.")
    history = False

    list_display = ("created_at", "challenger", "opponent", "status", "wins_a", "wins_b", "winner")
    list_filter = ("status",)
    list_select_related = ("challenger", "opponent", "winner")
    search_fields = ("challenger__username", "opponent__username")
    ordering = ("-created_at",)
    readonly_fields = ("challenger", "opponent", "meta", "lineup_a", "lineup_b", "rarities_a", "rarities_b", "games",
                       "draft", "wins_a", "wins_b", "auto_a", "auto_b", "missed_a", "missed_b", "deadline", "winner",
                       "forfeit", "finished_at")

    def has_add_permission(self, request):
        return False

"""Les formulaires de la gestion : importer des ligues, synchroniser la liste
des ligues.

Pages de formulaire du framework (django-generic/docs/form-pages.md) : elles
lancent une opération en tâche de fond puis ouvrent la page de son
exécution, où le rapport s'affiche quand elle se termine.
"""

from typing import Any

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from generic.api import FormSerializer
from generic.sites import ResourceFormPageView

from .models import League
from .tasks import import_league, regroup_splits_operation, sync_champions_operation, sync_leagues

#: Les ligues internationales de Leaguepedia ajoutées par la case à cocher.
INTERNATIONAL_REGION = "International"


def year_choices():
    current = timezone.localdate().year
    return [(year, str(year)) for year in range(current, 2010, -1)]


class ImportLeagueForm(FormSerializer):
    leagues = serializers.PrimaryKeyRelatedField(
        label=_("Ligues"),
        many=True,
        required=False,
        queryset=League.objects.all(),
        help_text=_(
            "Une ou plusieurs ligues (tape LEC, LCK, LFL…). Liste vide ? "
            "Lance d'abord « Synchroniser les ligues »."
        ),
    )
    years = serializers.MultipleChoiceField(
        label=_("Années"), choices=year_choices()
    )
    international = serializers.BooleanField(
        label=_("Ajouter les compétitions internationales (MSI, Worlds, First Stand…)"),
        default=False,
        required=False,
    )
    name_like = serializers.CharField(
        label=_("Filtre sur le nom du tournoi"),
        required=False,
        allow_blank=True,
        max_length=100,
        help_text=_("Facultatif : « Spring » pour n'importer que ce split."),
    )
    images = serializers.BooleanField(
        label=_("Télécharger les photos et les logos"),
        default=True,
        required=False,
    )
    include_qualifiers = serializers.BooleanField(
        label=_("Inclure les qualifications"),
        default=False,
        required=False,
        help_text=_(
            "Les playoffs, play-ins et « Road to MSI » sont toujours "
            "inclus ; les qualifications (montée, qualifs régionales) non."
        ),
    )

    def validate(self, attrs):
        if not attrs.get("leagues") and not attrs.get("international"):
            raise serializers.ValidationError(
                {"leagues": _("Choisis au moins une ligue (ou les compétitions internationales).")}
            )
        return attrs


class ImportLeaguePage(ResourceFormPageView):
    serializer_class = ImportLeagueForm
    description = _(
        "Cherche sur Leaguepedia les tournois des ligues choisies pour ces "
        "années, crée les splits, importe parties, joueurs et photos, puis "
        "génère les cartes et les boosters (un par région et par année). "
        "Compte quelques minutes par split ; la page de l'exécution montre "
        "le rapport à la fin."
    )
    icon = "travel_explore"
    submit_label = _("Lancer l'import")

    def get_initial(self) -> dict[str, Any]:
        return {"years": [timezone.localdate().year]}

    def form_valid(self, serializer: Any) -> Any:
        values = serializer.validated_data
        names = [league.name for league in values.get("leagues", [])]
        if values.get("international"):
            names += list(
                League.objects.filter(region=INTERNATIONAL_REGION, level="Primary").values_list("name", flat=True)
            )
        return import_league.start(
            self.request,
            background=True,
            leagues=sorted(set(names)),
            years=sorted(int(y) for y in values["years"]),
            name_like=values.get("name_like") or "",
            include_qualifiers=bool(values.get("include_qualifiers")),
            images=values.get("images", True),
        )

    def get_success_message(self, result: Any) -> str:
        return str(_("Import lancé : le rapport s'affichera sur cette page."))

    def get_success_url(self, result: Any) -> str:
        return result.get_absolute_url()


class SyncLeaguesForm(FormSerializer):
    names = serializers.CharField(
        label=_("Ligues"),
        required=False,
        allow_blank=True,
        max_length=500,
        help_text=_(
            "Noms courts ou complets, séparés par des virgules : « LEC, LCK, "
            "LFL, MSI, WCS ». Vide : selon la case ci-dessous."
        ),
    )
    primary_only = serializers.BooleanField(
        label=_("Seulement les ligues majeures (niveau Primary)"),
        default=True,
        required=False,
        help_text=_("Sans nom ci-dessus : ajoute toutes les ligues majeures (~30) plutôt que les ~200."),
    )

    def validate_names(self, value: str) -> list[str]:
        return [name.strip() for name in value.replace(";", ",").split(",") if name.strip()]


class SyncLeaguesPage(ResourceFormPageView):
    serializer_class = SyncLeaguesForm
    description = _(
        "Ajoute des ligues de Leaguepedia à la liste (nom, nom court, "
        "région, niveau) : seulement des infos, aucune partie importée. "
        "Elles apparaissent ensuite dans l'import et les boosters. Une ligue "
        "en trop se supprime depuis la liste des ligues."
    )
    icon = "sync"
    submit_label = _("Synchroniser")

    def form_valid(self, serializer: Any) -> Any:
        values = serializer.validated_data
        names = values.get("names") or []
        return sync_leagues.start(
            self.request,
            background=True,
            names=names,
            primary_only=bool(values.get("primary_only")) and not names,
        )

    def get_success_message(self, result: Any) -> str:
        return str(_("Synchronisation lancée."))

    def get_success_url(self, result: Any) -> str:
        return result.get_absolute_url()


class SyncChampionsForm(FormSerializer):
    images = serializers.BooleanField(
        label=_("Télécharger les images"),
        default=True,
        required=False,
        help_text=_("Icône, illustration et splash de chaque champion (~35 Mo la première fois)."),
    )
    force_images = serializers.BooleanField(
        label=_("Retélécharger les images déjà présentes"),
        default=False,
        required=False,
        help_text=_("Utile après une refonte visuelle d'un champion."),
    )


class SyncChampionsPage(ResourceFormPageView):
    serializer_class = SyncChampionsForm
    description = _(
        "Récupère tous les champions sur Data Dragon (Riot) : titre, "
        "classes, profil attaque / défense / magie, et leurs images. Le "
        "profil sert à la cohérence des compositions ; les images, à la "
        "sélection des champions dans les équipes."
    )
    icon = "sync"
    submit_label = _("Synchroniser")

    def form_valid(self, serializer: Any) -> Any:
        values = serializer.validated_data
        return sync_champions_operation.start(
            self.request,
            background=True,
            images=bool(values.get("images")),
            force_images=bool(values.get("force_images")),
        )

    def get_success_message(self, result: Any) -> str:
        return str(_("Synchronisation des champions lancée."))

    def get_success_url(self, result: Any) -> str:
        return result.get_absolute_url()


class RegroupSplitsForm(FormSerializer):
    pass


class RegroupSplitsPage(ResourceFormPageView):
    serializer_class = RegroupSplitsForm
    description = _(
        "Réunit les phases d'une même année de chaque ligue locale (Cup, "
        "splits, playoffs) en une seule période : « LCK 2026 ». Les "
        "exemplaires possédés gardent leur rareté et passent sur la carte "
        "de l'année ; les équipes suivent. Les compétitions "
        "internationales restent à part. Les nouveaux imports sont déjà "
        "regroupés : cette page sert aux données importées avant."
    )
    icon = "merge"
    submit_label = _("Regrouper")

    def form_valid(self, serializer: Any) -> Any:
        return regroup_splits_operation.start(self.request)

    def get_success_message(self, result: Any) -> str:
        return str(_("Regroupement terminé."))

    def get_success_url(self, result: Any) -> str:
        return result.get_absolute_url()

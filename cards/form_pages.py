"""Les formulaires de la gestion du jeu : offrir des boosters."""

from typing import Any

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from generic.api import FormSerializer
from generic.sites import ResourceFormPageView

from .models import Booster
from .services import MAX_GIFT, GameError, ensure_boosters, ensure_default_rarities, give_boosters


class GiveBoostersForm(FormSerializer):
    booster = serializers.PrimaryKeyRelatedField(
        label=_("Booster"),
        queryset=Booster.objects.filter(is_active=True),
    )
    quantity = serializers.IntegerField(
        label=_("Nombre de boosters"), min_value=1, max_value=MAX_GIFT, default=1,
        help_text=_("Par joueur. Ils s'ajoutent à sa réserve, sans plafond."),
    )
    users = serializers.PrimaryKeyRelatedField(
        label=_("Joueurs"),
        many=True,
        required=False,
        queryset=get_user_model().objects.filter(is_active=True),
        help_text=_("Un ou plusieurs joueurs du site (tape leur nom)."),
    )
    everyone = serializers.BooleanField(
        label=_("Tous les joueurs du site"),
        default=False,
        required=False,
        help_text=_("Ignore la liste ci-dessus et offre à tous les comptes actifs."),
    )
    message = serializers.CharField(
        label=_("Message"),
        required=False,
        allow_blank=True,
        max_length=200,
        help_text=_("Affiché au joueur à sa prochaine visite (facultatif)."),
    )

    def validate(self, data):
        if not data.get("everyone") and not data.get("users"):
            raise serializers.ValidationError({"users": _("Choisis au moins un joueur, ou coche « Tous les joueurs ».")})
        return data


class GiveBoostersPage(ResourceFormPageView):
    serializer_class = GiveBoostersForm
    description = _(
        "Offre des boosters à des joueurs du site. Ils s'ajoutent à leur "
        "réserve sans plafond ni recharge, et le joueur voit le cadeau à "
        "sa prochaine visite. L'historique est dans « Cadeaux de boosters »."
    )
    icon = "featured_seasonal_and_gifts"
    submit_label = _("Offrir")

    def form_valid(self, serializer: Any) -> Any:
        values = serializer.validated_data
        users = (
            get_user_model().objects.filter(is_active=True)
            if values.get("everyone")
            else values["users"]
        )
        try:
            count = give_boosters(
                users, values["booster"], values["quantity"],
                given_by=self.request.user, message=values.get("message", ""),
            )
        except GameError as exc:
            raise serializers.ValidationError({"quantity": str(exc)}) from exc
        return str(_("%(n)s booster(s) « %(booster)s » offert(s) à %(count)s joueur(s).") % {
            "n": values["quantity"], "booster": values["booster"].name, "count": count,
        })


class InitBoostersForm(FormSerializer):
    deactivate_obsolete = serializers.BooleanField(
        label=_("Désactiver les boosters automatiques d'un ancien découpage"),
        default=True,
        required=False,
        help_text=_("Par exemple l'ancien « International 2026 », remplacé par EWC et Riot International."),
    )


class InitBoostersPage(ResourceFormPageView):
    serializer_class = InitBoostersForm
    description = _(
        "Crée les boosters types à partir des périodes importées : « Tous "
        "les splits », un booster par région et par année, l'Esports World "
        "Cup par année et Riot International (First Stand, MSI, Worlds) par "
        "année. Les boosters existants gardent leurs réglages. Se lance "
        "aussi tout seul après chaque import et à chaque démarrage du site."
    )
    icon = "auto_awesome"
    submit_label = _("Créer les boosters types")

    def form_valid(self, serializer: Any) -> Any:
        ensure_default_rarities()
        created = ensure_boosters(deactivate_obsolete=serializer.validated_data.get("deactivate_obsolete", True))
        if not created:
            return str(_("Tous les boosters types existent déjà."))
        return str(_("Créés : %(names)s.") % {"names": ", ".join(created)})

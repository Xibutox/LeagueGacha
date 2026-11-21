"""Le jeu à la racine, la gestion (django-generic) sous /gestion/.

Le site du framework doit rester le dernier motif de l'URLconf.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

from cards import views as card_views
from generic.sites import site

urlpatterns = [
    # Les traductions côté navigateur du framework.
    path(
        "jsi18n/",
        JavaScriptCatalog.as_view(packages=["generic"]),
        name="javascript-catalog",
    ),
    # Notifications, préférences, vues enregistrées du framework.
    path("api/generic/", include("generic.urls", namespace="generic")),
    # Le jeu : comptes, boosters, collection.
    path(
        "compte/connexion/",
        auth_views.LoginView.as_view(),
        name="login",
    ),
    path(
        "compte/deconnexion/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
    path("compte/inscription/", card_views.signup, name="signup"),
    path("", include("cards.urls")),
]

if settings.DEBUG:
    # Photos et logos du jeu (en production : servis par le proxy).
    urlpatterns += static(
        settings.GAME_MEDIA_URL, document_root=settings.GAME_MEDIA_ROOT
    )

# La gestion : tableau de bord, écrans générés, API. En dernier.
urlpatterns.append(path("gestion/", site.urls))

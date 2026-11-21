"""Application ASGI : HTTP et, si Channels est installé, les WebSockets du
framework (notifications, tableaux qui se mettent à jour). Copié de
django-generic/example_project/asgi.py.

Settings de production par défaut : un serveur lancé sans
DJANGO_SETTINGS_MODULE ne doit jamais tourner avec DEBUG.
"""

import importlib.util
import os

from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

django_asgi_application = get_asgi_application()
http_application = django_asgi_application

if settings.DEBUG:
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

    http_application = ASGIStaticFilesHandler(django_asgi_application)

if importlib.util.find_spec("channels") is not None:
    from channels.auth import AuthMiddlewareStack
    from channels.routing import ProtocolTypeRouter, URLRouter
    from channels.security.websocket import AllowedHostsOriginValidator

    from generic.events.routing import websocket_urlpatterns

    application = ProtocolTypeRouter(
        {
            "http": http_application,
            "websocket": AllowedHostsOriginValidator(
                AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
            ),
        }
    )
else:
    application = http_application

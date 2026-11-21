"""Stockage public des photos de joueurs et des logos d'équipes.

Les fichiers de MEDIA_ROOT sont privés (django-generic les sert par les
endpoints de chaque enregistrement, en vérifiant les permissions). Les
images du jeu, elles, sont affichées à tous les joueurs : elles vont dans
GAME_MEDIA_ROOT, servi en direct sous GAME_MEDIA_URL.
"""

from django.conf import settings
from django.core.files.storage import FileSystemStorage


def game_storage():
    # Un callable : la migration le référence par son nom, pas par sa valeur.
    return FileSystemStorage(
        location=settings.GAME_MEDIA_ROOT, base_url=settings.GAME_MEDIA_URL
    )

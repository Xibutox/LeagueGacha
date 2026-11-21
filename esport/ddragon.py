"""Data Dragon, les données publiques de Riot : la liste des champions, leur
titre, leurs classes (Fighter, Tank, Mage…), leur profil attaque / défense
/ magie, et leurs illustrations.

Sert à deux choses : de belles images à la sélection des champions, et le
calcul de cohérence d'une composition (scoring.composition_coherence).
"""

from __future__ import annotations

import logging
from typing import Callable

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from .champions import champion_key, portrait_url, splash_url
from .models import Champion

logger = logging.getLogger(__name__)

BASE = "https://ddragon.leagueoflegends.com"


class DataDragonError(Exception):
    pass


class DataDragonClient:
    def __init__(self, session: requests.Session | None = None, timeout: float = 20):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", settings.LEAGUEPEDIA_USER_AGENT)
        self.timeout = timeout

    def _get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=self.timeout)
        if response.status_code != 200:
            raise DataDragonError(f"{url} : HTTP {response.status_code}")
        return response

    def latest_version(self) -> str:
        versions = self._get(f"{BASE}/api/versions.json").json()
        if not versions:
            raise DataDragonError("Aucune version de Data Dragon.")
        return versions[0]

    def champions(self, version: str, language: str = "en_US") -> dict:
        return self._get(f"{BASE}/cdn/{version}/data/{language}/champion.json").json()["data"]

    def download(self, url: str) -> bytes:
        return self._get(url).content


def icon_file_url(version: str, key: str) -> str:
    return f"{BASE}/cdn/{version}/img/champion/{key}.png"


def sync_champions(
    client: DataDragonClient | None = None,
    images: bool = True,
    force_images: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Crée ou complète tous les champions de Data Dragon, et télécharge
    leurs images (icône, illustration en hauteur, splash) dans GAME_MEDIA.

    Les champions déjà créés par l'import (nom Leaguepedia) sont retrouvés
    par leur identifiant Riot, puis par leur nom."""
    client = client or DataDragonClient()
    log = log or (lambda message: None)
    version = client.latest_version()
    english = client.champions(version, "en_US")
    try:
        french = client.champions(version, "fr_FR")
    except (DataDragonError, requests.RequestException):
        french = {}
    log(f"Data Dragon {version} : {len(english)} champions")

    by_key = {c.key.lower(): c for c in Champion.objects.exclude(key="")}
    by_name = {c.name.lower(): c for c in Champion.objects.all()}
    created = updated = downloaded = 0
    failures = 0
    for key, data in sorted(english.items()):
        champion = by_key.get(key.lower()) or by_name.get(data["name"].lower())
        if champion is None:
            # Un nom déjà pris avec un autre identifiant : on corrige l'identifiant.
            champion = Champion(name=data["name"])
            created += 1
        else:
            updated += 1
        info = data.get("info") or {}
        champion.key = key
        champion.title = (french.get(key) or data).get("title", "")
        champion.tags = list(data.get("tags") or [])
        champion.attack = int(info.get("attack") or 0)
        champion.defense = int(info.get("defense") or 0)
        champion.magic = int(info.get("magic") or 0)
        champion.difficulty = int(info.get("difficulty") or 0)
        champion.synced_at = timezone.now()
        champion.save()

        if not images or failures >= 5:
            continue
        for field, url in (
            ("icon", icon_file_url(version, key)),
            ("portrait", portrait_url(key)),
            ("splash", splash_url(key)),
        ):
            if getattr(champion, field) and not force_images:
                continue
            try:
                content = client.download(url)
            except (DataDragonError, requests.RequestException) as exc:
                failures += 1
                log(f"! {champion.name} ({field}) : {exc}")
                if failures >= 5:
                    log("! Trop d'échecs : les images restantes sont ignorées (les liens Data Dragon servent en attendant).")
                    break
                continue
            failures = 0
            extension = url.rsplit(".", 1)[-1]
            getattr(champion, field).save(f"{key}.{extension}", ContentFile(content), save=True)
            downloaded += 1

    # Les champions des scoreboards absents de Data Dragon (nom mal écrit…).
    missing = list(
        Champion.objects.filter(synced_at__isnull=True).values_list("name", flat=True)
    )
    for name in missing:
        log(f"! {name} : introuvable dans Data Dragon (identifiant à corriger : {champion_key(name)} ?)")
    return {"version": version, "created": created, "updated": updated, "images": downloaded, "missing": missing}

"""Client minimal pour l'API de Leaguepedia (lol.fandom.com).

- `cargo()` interroge les tables Cargo (sorte de SQL en lecture seule) et gère
  la pagination (500 lignes max par requête pour un client anonyme).
- `image_urls()` transforme des noms de fichiers (« Caps 2025 Split 1.png »)
  en URLs téléchargeables (miniature à la largeur voulue).

Ce module ne dépend pas de Django : il est testable seul.
"""
from __future__ import annotations

import html
import logging
import time
from typing import Callable, Iterable

import requests

logger = logging.getLogger(__name__)

API_URL = "https://lol.fandom.com/api.php"
CARGO_PAGE_SIZE = 500
IMAGEINFO_BATCH = 50


class LeaguepediaError(Exception):
    pass


def quote(value: str) -> str:
    """Échappe une chaîne pour une clause WHERE Cargo (entre guillemets doubles)."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def in_clause(field: str, values: Iterable[str]) -> str:
    values = list(values)
    if not values:
        return "1=0"
    return f"{field} IN ({', '.join(quote(v) for v in values)})"


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _clean(value):
    if isinstance(value, str):
        return html.unescape(value).strip()
    return value


class LeaguepediaClient:
    def __init__(
        self,
        user_agent: str = "WikiLolEsport/0.1",
        delay: float = 1.5,
        session: requests.Session | None = None,
        image_session: requests.Session | None = None,
        max_retries: int = 6,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        # Session à part pour le CDN des images : sans les cookies de l'API.
        self.image_session = image_session or requests.Session()
        #: Index du jeu d'en-têtes qui a marché pour les images (None : pas encore).
        self.image_strategy: int | None = None
        self.delay = delay
        self.max_retries = max_retries
        self._sleep = sleep
        self._last_call = 0.0

    # ------------------------------------------------------------------ HTTP
    def _throttle(self):
        wait = self.delay - (time.monotonic() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.monotonic()

    def get(self, params: dict) -> dict:
        params = {"format": "json", **params}
        backoff = max(self.delay, 1.0) * 5
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(API_URL, params=params, timeout=60)
            except requests.RequestException as exc:
                logger.warning("Erreur réseau (%s), tentative %s/%s", exc, attempt, self.max_retries)
                self._sleep(backoff * attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                logger.warning("HTTP %s, nouvelle tentative dans %ss", resp.status_code, backoff * attempt)
                self._sleep(backoff * attempt)
                continue
            if resp.status_code != 200:
                raise LeaguepediaError(f"HTTP {resp.status_code} : {resp.text[:300]}")
            data = resp.json()
            error = data.get("error")
            if error:
                if error.get("code") in {"ratelimited", "maxlag"}:
                    logger.warning("Limite de débit Leaguepedia atteinte, pause de %ss", backoff * attempt)
                    self._sleep(backoff * attempt)
                    continue
                raise LeaguepediaError(f"{error.get('code')}: {error.get('info')}")
            return data
        raise LeaguepediaError(f"Échec après {self.max_retries} tentatives : {params}")

    # ----------------------------------------------------------------- Cargo
    def cargo(
        self,
        tables: str,
        fields: str,
        where: str | None = None,
        join_on: str | None = None,
        order_by: str | None = None,
        group_by: str | None = None,
        max_rows: int | None = None,
    ) -> list[dict]:
        """Exécute une requête Cargo et renvoie toutes les lignes (pagination incluse).

        Les champs doivent être aliasés (« SP.Kills=Kills ») pour obtenir des clés
        propres dans le résultat.
        """
        rows: list[dict] = []
        offset = 0
        while True:
            limit = CARGO_PAGE_SIZE if max_rows is None else min(CARGO_PAGE_SIZE, max_rows - len(rows))
            if limit <= 0:
                break
            params = {
                "action": "cargoquery",
                "tables": tables,
                "fields": fields,
                "limit": limit,
                "offset": offset,
            }
            if where:
                params["where"] = where
            if join_on:
                params["join_on"] = join_on
            if order_by:
                params["order_by"] = order_by
            if group_by:
                params["group_by"] = group_by
            data = self.get(params)
            page = [
                {k: _clean(v) for k, v in item.get("title", {}).items()}
                for item in data.get("cargoquery", [])
            ]
            rows.extend(page)
            if len(page) < limit:
                break
            offset += len(page)
        return rows

    # ---------------------------------------------------------------- Images
    def image_infos(self, filenames: Iterable[str], width: int | None = None) -> dict[str, dict]:
        """Renvoie {nom de fichier: {"thumb": URL miniature, "url": URL originale}}.
        Les fichiers introuvables sont absents du résultat."""
        names = sorted({f for f in filenames if f})
        result: dict[str, dict] = {}
        for batch in chunked(names, IMAGEINFO_BATCH):
            params = {
                "action": "query",
                "titles": "|".join(f"File:{name}" for name in batch),
                "prop": "imageinfo",
                "iiprop": "url",
            }
            if width:
                params["iiurlwidth"] = width
            data = self.get(params)
            query = data.get("query", {})
            # MediaWiki normalise les titres (underscores → espaces, 1re lettre en majuscule).
            normalized = {n["to"]: n["from"] for n in query.get("normalized", [])}
            for page in query.get("pages", {}).values():
                info = (page.get("imageinfo") or [None])[0]
                if not info:
                    continue
                title = page.get("title", "")
                original = normalized.get(title, title)
                name = original.split(":", 1)[1] if ":" in original else original
                result[name] = {"thumb": info.get("thumburl") or info.get("url"), "url": info.get("url")}
        return result

    def image_urls(self, filenames: Iterable[str], width: int | None = None) -> dict[str, str]:
        """Renvoie {nom de fichier: URL à télécharger (miniature si possible)}."""
        return {name: info["thumb"] for name, info in self.image_infos(filenames, width).items()}

    # Les images sont servies par le CDN de Fandom (static.wikia.nocookie.net),
    # qui ne suit pas les mêmes règles que l'API : il refuse (403) certaines
    # requêtes que l'API accepte. On essaie plusieurs jeux d'en-têtes, tous
    # honnêtes (ils nomment le projet), et on garde le premier qui marche.
    def _image_header_strategies(self) -> list[tuple[str, dict]]:
        project = self.session.headers.get("User-Agent", "WikiLolEsport")
        accept = "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.5"
        compatible = f"Mozilla/5.0 (compatible; {project})"
        return [
            ("agent du projet", {"User-Agent": project, "Accept": accept}),
            ("agent compatible", {"User-Agent": compatible, "Accept": accept}),
            (
                "agent compatible + referer",
                {"User-Agent": compatible, "Accept": accept, "Referer": "https://lol.fandom.com/"},
            ),
        ]

    def download(self, url: str, fallback_url: str | None = None) -> bytes:
        """Télécharge une image. Essaie la miniature puis l'originale, avec
        chaque jeu d'en-têtes ; retient celui qui a marché pour la suite.
        Lève ImageDownloadError avec le détail de chaque tentative."""
        strategies = self._image_header_strategies()
        if self.image_strategy is not None:
            order = [strategies[self.image_strategy]] + [
                s for i, s in enumerate(strategies) if i != self.image_strategy
            ]
        else:
            order = strategies
        urls = [u for u in dict.fromkeys([url, fallback_url]) if u]
        attempts = []
        for name, headers in order:
            for candidate in urls:
                self._throttle()
                try:
                    resp = self.image_session.get(candidate, headers=headers, timeout=60)
                except requests.RequestException as exc:
                    attempts.append(f"{name} : {exc.__class__.__name__}")
                    continue
                kind = resp.headers.get("Content-Type", "")
                if resp.status_code == 200 and kind.startswith("image/"):
                    self.image_strategy = [s[0] for s in strategies].index(name)
                    return resp.content
                label = "miniature" if candidate == url else "originale"
                attempts.append(f"{name} ({label}) : HTTP {resp.status_code} {kind}".strip())
        raise ImageDownloadError("; ".join(attempts))


class ImageDownloadError(LeaguepediaError):
    pass

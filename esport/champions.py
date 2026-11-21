"""Nom de champion (tel que Leaguepedia l'écrit) → identifiant Riot
(Data Dragon), qui sert à afficher l'icône.

Riot retire espaces, points et apostrophes, avec des exceptions : la casse
après l'apostrophe (« Kai'Sa » → Kaisa mais « Kog'Maw » → KogMaw) et
quelques noms qui n'ont rien à voir (Wukong → MonkeyKing)."""

import re

OVERRIDES = {
    "wukong": "MonkeyKing",
    "nunu & willump": "Nunu",
    "nunu": "Nunu",
    "renata glasc": "Renata",
    "kai'sa": "Kaisa",
    "kha'zix": "Khazix",
    "cho'gath": "Chogath",
    "vel'koz": "Velkoz",
    "bel'veth": "Belveth",
    "kog'maw": "KogMaw",
    "rek'sai": "RekSai",
    "k'sante": "KSante",
    "leblanc": "Leblanc",
    "dr. mundo": "DrMundo",
    "jarvan iv": "JarvanIV",
    "fiddlesticks": "Fiddlesticks",
    "master yi": "MasterYi",
    "miss fortune": "MissFortune",
    "twisted fate": "TwistedFate",
    "xin zhao": "XinZhao",
    "lee sin": "LeeSin",
    "tahm kench": "TahmKench",
    "aurelion sol": "AurelionSol",
}

#: L'icône carrée, toujours à jour (CommunityDragon suit les patchs de Riot).
ICON_URL = "https://cdn.communitydragon.org/latest/champion/{key}/square"


def champion_key(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return ""
    override = OVERRIDES.get(text.lower())
    if override:
        return override
    words = re.split(r"[\s.]+", text)
    return "".join(w[:1].upper() + w[1:] for w in words if w).replace("'", "")


#: Les illustrations de Data Dragon (les mêmes pour tous les patchs).
PORTRAIT_URL = "https://ddragon.leagueoflegends.com/cdn/img/champion/loading/{key}_0.jpg"
SPLASH_URL = "https://ddragon.leagueoflegends.com/cdn/img/champion/centered/{key}_0.jpg"


def icon_url(key: str) -> str:
    return ICON_URL.format(key=key) if key else ""


def portrait_url(key: str) -> str:
    return PORTRAIT_URL.format(key=key) if key else ""


def splash_url(key: str) -> str:
    return SPLASH_URL.format(key=key) if key else ""

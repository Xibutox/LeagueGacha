"""Nom de pays (tel que Leaguepedia l'écrit, en anglais) → code ISO 3166-1
alpha-2, pour afficher un drapeau. Les nations du Royaume-Uni ont les codes
de flagcdn (gb-eng, gb-sct, gb-wls, gb-nir)."""

import unicodedata

COUNTRIES = {
    "afghanistan": "af", "albania": "al", "algeria": "dz", "andorra": "ad", "angola": "ao",
    "argentina": "ar", "armenia": "am", "australia": "au", "austria": "at", "azerbaijan": "az",
    "bahrain": "bh", "bangladesh": "bd", "belarus": "by", "belgium": "be", "bolivia": "bo",
    "bosnia and herzegovina": "ba", "bosnia": "ba", "brazil": "br", "brunei": "bn", "bulgaria": "bg",
    "cambodia": "kh", "cameroon": "cm", "canada": "ca", "chile": "cl", "china": "cn",
    "colombia": "co", "costa rica": "cr", "croatia": "hr", "cuba": "cu", "cyprus": "cy",
    "czech republic": "cz", "czechia": "cz", "denmark": "dk", "dominican republic": "do",
    "ecuador": "ec", "egypt": "eg", "el salvador": "sv", "estonia": "ee", "finland": "fi",
    "france": "fr", "georgia": "ge", "germany": "de", "ghana": "gh", "greece": "gr",
    "guatemala": "gt", "honduras": "hn", "hong kong": "hk", "hungary": "hu", "iceland": "is",
    "india": "in", "indonesia": "id", "iran": "ir", "iraq": "iq", "ireland": "ie",
    "israel": "il", "italy": "it", "jamaica": "jm", "japan": "jp", "jordan": "jo",
    "kazakhstan": "kz", "kenya": "ke", "kosovo": "xk", "kuwait": "kw", "kyrgyzstan": "kg",
    "laos": "la", "latvia": "lv", "lebanon": "lb", "libya": "ly", "liechtenstein": "li",
    "lithuania": "lt", "luxembourg": "lu", "macau": "mo", "macao": "mo", "malaysia": "my",
    "malta": "mt", "mexico": "mx", "moldova": "md", "monaco": "mc", "mongolia": "mn",
    "montenegro": "me", "morocco": "ma", "myanmar": "mm", "nepal": "np", "netherlands": "nl",
    "new zealand": "nz", "nicaragua": "ni", "nigeria": "ng", "north macedonia": "mk",
    "macedonia": "mk", "norway": "no", "oman": "om", "pakistan": "pk", "panama": "pa",
    "paraguay": "py", "peru": "pe", "philippines": "ph", "poland": "pl", "portugal": "pt",
    "puerto rico": "pr", "qatar": "qa", "romania": "ro", "russia": "ru", "saudi arabia": "sa",
    "senegal": "sn", "serbia": "rs", "singapore": "sg", "slovakia": "sk", "slovenia": "si",
    "south africa": "za", "south korea": "kr", "korea": "kr", "republic of korea": "kr",
    "spain": "es", "sri lanka": "lk", "sweden": "se", "switzerland": "ch", "syria": "sy",
    "taiwan": "tw", "chinese taipei": "tw", "tajikistan": "tj", "thailand": "th",
    "tunisia": "tn", "turkey": "tr", "turkiye": "tr", "turkmenistan": "tm", "ukraine": "ua",
    "united arab emirates": "ae", "uae": "ae", "united kingdom": "gb", "uk": "gb",
    "great britain": "gb", "england": "gb-eng", "scotland": "gb-sct", "wales": "gb-wls",
    "northern ireland": "gb-nir", "united states": "us", "usa": "us",
    "united states of america": "us", "uruguay": "uy", "uzbekistan": "uz", "venezuela": "ve",
    "vietnam": "vn", "viet nam": "vn", "faroe islands": "fo", "greenland": "gl",
}

#: Les noms français à afficher en info-bulle (les plus courants en esport).
FRENCH = {
    "kr": "Corée du Sud", "cn": "Chine", "tw": "Taïwan", "vn": "Viêt Nam", "jp": "Japon",
    "fr": "France", "de": "Allemagne", "es": "Espagne", "gb": "Royaume-Uni", "us": "États-Unis",
    "ca": "Canada", "br": "Brésil", "dk": "Danemark", "se": "Suède", "pl": "Pologne",
    "be": "Belgique", "nl": "Pays-Bas", "it": "Italie", "pt": "Portugal", "cz": "Tchéquie",
    "sk": "Slovaquie", "si": "Slovénie", "hr": "Croatie", "rs": "Serbie", "gr": "Grèce",
    "tr": "Turquie", "ru": "Russie", "ua": "Ukraine", "au": "Australie", "nz": "Nouvelle-Zélande",
    "hk": "Hong Kong", "mo": "Macao", "ph": "Philippines", "th": "Thaïlande", "my": "Malaisie",
    "sg": "Singapour", "id": "Indonésie", "mx": "Mexique", "ar": "Argentine", "cl": "Chili",
    "pe": "Pérou", "co": "Colombie", "ch": "Suisse", "at": "Autriche", "no": "Norvège",
    "fi": "Finlande", "ie": "Irlande", "hu": "Hongrie", "ro": "Roumanie", "bg": "Bulgarie",
    "lt": "Lituanie", "lv": "Lettonie", "ee": "Estonie", "il": "Israël", "ma": "Maroc",
    "dz": "Algérie", "tn": "Tunisie", "gb-eng": "Angleterre", "gb-sct": "Écosse",
    "gb-wls": "Pays de Galles",
}


def _key(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return " ".join(text.lower().replace("-", " ").split())


def country_code(name: str) -> str:
    """Code du drapeau pour un nom de pays, ou "" si inconnu.

    Accepte aussi une liste « France, Spain » (on prend le premier) et un
    code déjà sur deux lettres."""
    if not name:
        return ""
    first = name.split(",")[0].split(";")[0]
    key = _key(first)
    if key in COUNTRIES:
        return COUNTRIES[key]
    if len(key) == 2 and key.isalpha():
        return key
    return ""


def country_label(name: str) -> str:
    code = country_code(name)
    return FRENCH.get(code, (name or "").split(",")[0].strip())

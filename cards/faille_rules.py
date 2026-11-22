"""La Faille : les règles du mini-jeu, sans Django (testables seules).

Le combat se joue dans le navigateur (static/js/faille-engine.js, qui
reprend les mêmes formules). Le serveur s'en sert pour générer la suite
d'ennemis de la semaine, lire les objets de Data Dragon, calculer les
stats d'un build et vérifier qu'une progression déclarée est possible.
"""
from __future__ import annotations

import hashlib
import math
import random
import re

# --------------------------------------------------------------------- Ennemis
#: type → réglages. hp / dps : coefficients ; dmg : physical | magic ;
#: gold : or (sbires seulement) ; rate : poids de tirage aux étapes ordinaires ;
#: min_step : première étape où il peut sortir ; buff : récompense (monstres).
ENEMIES = {
    "melee": {"name": "Sbire de mêlée", "hp": 1.0, "dps": 1.0, "dmg": "physical",
              "gold": 1.3, "rate": 39, "wiki": ["Order_Minion_Melee_Render.png", "Blue_Melee_MinionSquare.png"]},
    "caster": {"name": "Sbire caster", "hp": 0.8, "dps": 1.2, "dmg": "magic",
               "gold": 0.9, "rate": 30, "wiki": ["Order_Minion_Caster_Render.png", "Blue_Caster_MinionSquare.png"]},
    "siege": {"name": "Sbire canon", "hp": 3.0, "dps": 1.5, "dmg": "physical",
              "gold": 3.2, "rate": 12, "min_step": 3, "wiki": ["Order_Minion_Siege_Render.png", "Blue_Siege_MinionSquare.png"]},
    "super": {"name": "Super sbire", "hp": 6.0, "dps": 2.5, "dmg": "physical",
              "gold": 5, "rate": 3, "min_step": 150, "wiki": ["Order_Minion_Super_Render.png", "Blue_Super_MinionSquare.png"]},
    "gromp": {"min_step": 5, "name": "Gromp", "hp": 2.0, "dps": 1.1, "dmg": "magic", "rate": 3,
              "buff": {"label": "Poison de Gromp : +20 % de dégâts passifs", "seconds": 90, "passive_pct": 20},
              "wiki": ["Gromp_Render.png", "GrompSquare.png"]},
    "wolves": {"min_step": 5, "name": "Loups des ombres", "hp": 2.0, "dps": 1.4, "dmg": "physical", "rate": 3,
               "buff": {"label": "Meute : +50 % d'or des sbires", "seconds": 90, "gold_pct": 50},
               "wiki": ["Greater_Murk_Wolf_Render.png", "Greater_Murk_WolfSquare.png"]},
    "raptors": {"min_step": 5, "name": "Raptors écarlates", "hp": 2.0, "dps": 1.4, "dmg": "physical", "rate": 3,
                "buff": {"label": "Instinct de chasse : +15 % de chances de coup critique", "seconds": 90, "crit": 15},
                "wiki": ["Crimson_Raptor_Render.png", "Crimson_RaptorSquare.png"]},
    "krugs": {"min_step": 5, "name": "Krugs antiques", "hp": 2.5, "dps": 1.3, "dmg": "physical", "rate": 3,
              "buff": {"label": "Peau de pierre : +30 % de dégâts par clic", "seconds": 60, "ad_pct": 30},
              "wiki": ["Ancient_Krug_Render.png", "Ancient_KrugSquare.png"]},
    "scuttle": {"min_step": 5, "name": "Carapateur", "hp": 1.5, "dps": 0.0, "dmg": "physical", "rate": 2,
                "buff": {"label": "Sanctuaire : +25 % d'or des sbires", "seconds": 120, "gold_pct": 25},
                "wiki": ["Rift_Scuttler_Render.png", "Rift_ScuttlerSquare.png"]},
    "blue": {"min_step": 5, "name": "Sentinelle bleue", "hp": 3.0, "dps": 1.6, "dmg": "magic", "rate": 1,
             "buff": {"label": "Buff bleu : +30 % de dégâts passifs", "seconds": 120, "passive_pct": 30},
             "wiki": ["Blue_Sentinel_Render.png", "Blue_SentinelSquare.png"]},
    "red": {"min_step": 5, "name": "Ronceraie rouge", "hp": 3.0, "dps": 1.8, "dmg": "physical", "rate": 1,
            "buff": {"label": "Buff rouge : les clics brûlent (+40 % en dégâts bruts) et la régénération double", "seconds": 120,
                     "burn_pct": 40, "regen_pct": 100},
            "wiki": ["Red_Brambleback_Render.png", "Red_BramblebackSquare.png"]},
    # Jalons
    "infernal": {"name": "Drake infernal", "hp": 2.0, "dps": 1.0, "dmg": "magic", "epic": True,
                 "wiki": ["Infernal_Drake_Render.png", "Infernal_DrakeSquare.png"]},
    "mountain": {"name": "Drake des montagnes", "hp": 2.0, "dps": 1.0, "dmg": "physical", "epic": True,
                 "wiki": ["Mountain_Drake_Render.png", "Mountain_DrakeSquare.png"]},
    "ocean": {"name": "Drake des océans", "hp": 2.0, "dps": 1.0, "dmg": "magic", "epic": True,
              "wiki": ["Ocean_Drake_Render.png", "Ocean_DrakeSquare.png"]},
    "cloud": {"name": "Drake des nuages", "hp": 2.0, "dps": 1.0, "dmg": "magic", "epic": True,
              "wiki": ["Cloud_Drake_Render.png", "Cloud_DrakeSquare.png"]},
    "hextech": {"name": "Drake hextech", "hp": 2.0, "dps": 1.0, "dmg": "magic", "epic": True,
                "wiki": ["Hextech_Drake_Render.png", "Hextech_DrakeSquare.png"]},
    "chemtech": {"name": "Drake techno-chimique", "hp": 2.0, "dps": 1.0, "dmg": "magic", "epic": True,
                 "wiki": ["Chemtech_Drake_Render.png", "Chemtech_DrakeSquare.png"]},
    "herald": {"name": "Héraut de la Faille", "hp": 2.5, "dps": 1.1, "dmg": "physical", "epic": True,
               "buff": {"label": "Œil du Héraut : or ×2", "seconds": 180, "gold_pct": 100},
               "wiki": ["Rift_Herald_Render.png", "Rift_HeraldSquare.png"]},
    "grubs": {"name": "Larves du Néant", "hp": 2.5, "dps": 1.1, "dmg": "magic", "epic": True,
              "wiki": ["Voidgrub_Render.png", "VoidgrubSquare.png"]},
    "baron": {"name": "Baron Nashor", "hp": 3.0, "dps": 1.2, "dmg": "physical", "epic": True,
              "buff": {"label": "Main du Baron : +40 % de dégâts par clic et de puissance", "seconds": 180,
                       "ad_pct": 40, "ap_pct": 40},
              "wiki": ["Baron_Nashor_Render.png", "Baron_Nashor_Render_old.png", "Baron_NashorSquare.png"]},
    "elder": {"name": "Dragon ancestral", "hp": 3.0, "dps": 1.2, "dmg": "magic", "epic": True,
              "buff": {"label": "Aspect du dragon : exécute les ennemis sous 20 % de PV", "seconds": 180, "execute": 20},
              "wiki": ["Elder_Dragon_Render.png", "Elder_DragonSquare.png"]},
}
DRAKES = ["infernal", "mountain", "ocean", "cloud", "hextech", "chemtech"]
CAMPS = ["gromp", "wolves", "raptors", "krugs", "scuttle", "blue", "red"]
MINIONS = ["melee", "caster", "siege", "super"]
MONSTERS = [k for k in ENEMIES if k not in MINIONS]

#: Le buff de chaque drake, pour la semaine (cumulable) ; l'âme double ceux de son type.
DRAKE_BUFFS = {
    "infernal": {"label": "+6 % de dégâts d'attaque et de puissance", "ad_pct": 6, "ap_pct": 6},
    "mountain": {"label": "+6 % de PV max", "hp_pct": 6},
    "ocean": {"label": "+3 PV rendus par seconde", "regen_flat": 3},
    "cloud": {"label": "+4 % de chances de coup critique", "crit": 4},
    "hextech": {"label": "+8 % de dégâts passifs", "passive_pct": 8},
    "chemtech": {"label": "jusqu'à +10 % de dégâts quand tes PV baissent", "low_hp_dmg": 10},
}

#: Croissance par étape (calée par le simulateur, voir README) :
#: PV = coef × (HP_BASE + HP_C × étape^HP_P) ; dégâts = coef × (DPS_BASE + DPS_PER_STEP × étape).
HP_BASE = 60.0
HP_C = 0.12
HP_P = 2.48
DPS_BASE = 4.0
DPS_PER_STEP = 0.08
GOLD_PER_STEP = 0.02  # +2 % d'or par étape
#: Un sbire trop bas sous ta meilleure étape rapporte moins : -5 % par étape d'écart (min 10 %).
FARM_PENALTY_PER_STEP = 0.05
FARM_MIN = 0.1
ERRANT_BARON_CHANCE = 0.004
DRAKE_EVERY = 20
HERALD_EVERY = 40
BARON_EVERY = 60


def week_seed(week_iso: str) -> int:
    return int(hashlib.sha256(f"faille-{week_iso}".encode()).hexdigest()[:12], 16)


def enemy_kind(seed: int, step: int) -> tuple[str, bool]:
    """(type, errant) de l'ennemi à cette étape : le même pour tout le monde dans la semaine."""
    rng = random.Random(seed * 1_000_003 + step)
    if step > 0 and step % BARON_EVERY == 0:
        return ("baron", False)
    if step > 0 and step % HERALD_EVERY == 0:
        return (rng.choice(["herald", "grubs"]), False)
    if step > 0 and step % DRAKE_EVERY == 0:
        return (rng.choice(DRAKES), False)
    if step > 10 and rng.random() < ERRANT_BARON_CHANCE:
        return ("baron", True)
    kinds = [k for k in MINIONS + CAMPS if step >= ENEMIES[k].get("min_step", 0)]
    return (rng.choices(kinds, weights=[ENEMIES[k]["rate"] for k in kinds])[0], False)


def enemy_at(seed: int, step: int, elder: bool = False) -> dict:
    """Les stats de l'ennemi d'une étape. elder : l'âme du dragon est prise,
    le jalon des 100 devient le Dragon ancestral."""
    kind, errant = enemy_kind(seed, step)
    if kind == "baron" and elder and not errant:
        kind = "elder"
    spec = ENEMIES[kind]
    hp = spec["hp"] * (HP_BASE + HP_C * step ** HP_P)
    return {
        "step": step,
        "kind": kind,
        "errant": errant,
        "hp": round(hp),
        "dps": round(spec["dps"] * (DPS_BASE + DPS_PER_STEP * step), 1),
        "dmg": spec["dmg"],
        "gold": round(spec.get("gold", 0) * (1 + GOLD_PER_STEP * step), 1),
    }


def sequence(seed: int, start: int, count: int, elder: bool = False) -> list[dict]:
    return [enemy_at(seed, s, elder) for s in range(max(0, start), max(0, start) + count)]


# --------------------------------------------------------------------- Objets
#: Les stats de la Faille (tout le reste a été retiré pour garder le jeu simple).
STAT_NAMES = {
    "ad": "dégâts par clic", "ap": "puissance (dégâts passifs)", "crit": "% de chances de coup critique (clics)",
    "critdmg": "% de dégâts critiques", "hp": "PV", "regen": "PV rendus par seconde", "lifesteal": "% de vol de vie (clics)",
    "goldgen": "PO toutes les 10 s", "gold": "% d'or des sbires",
}
#: Dégâts passifs par seconde par point de puissance.
AP_DPS = 2.5
ELIXIR_MINUTES = 10
#: Chaque élixir acheté dans la semaine coûte 30 % de plus que le précédent.
ELIXIR_PRICE_GROWTH = 1.3
ATLAS_ID, ATLAS_UPGRADE = "3865", "3869"
ELIXIR_IDS = {"2138", "2139", "2140"}

#: Les quatre branches. Physique = dégâts des clics, magique = dégâts
#: passifs (sans cliquer), tank = des combats plus longs, support = de l'or.
BRANCHES = {
    "clic": "Clic",
    "passif": "Passif",
    "tank": "Tank",
    "support": "Support",
}
FORGE_CLASSES = {
    "Clic": "les objets physiques (dégâts des clics)",
    "Passif": "les objets magiques (dégâts passifs)",
    "Tank": "les objets de PV",
    "Support": "les objets de support (or)",
}

#: Les objets de la Faille : les plus iconiques de chaque rôle, avec leurs
#: stats de Faille (Data Dragon ne sert plus que pour le nom, l'icône, le prix
#: et la recette). id → (branche, stats).
CURATED = {
    # Clic (physique)
    "1036": ("clic", {"ad": 10}),
    "1037": ("clic", {"ad": 25}),
    "1038": ("clic", {"ad": 40}),
    "1018": ("clic", {"crit": 15}),
    "1053": ("clic", {"ad": 15, "lifesteal": 7}),
    "3070": ("clic", {}),
    "3031": ("clic", {"ad": 65, "crit": 25}),
    "3153": ("clic", {"ad": 40, "lifesteal": 10}),
    "3072": ("clic", {"ad": 80, "lifesteal": 15}),
    "6672": ("clic", {"ad": 50, "crit": 15}),
    "6676": ("clic", {"ad": 55, "crit": 25}),
    "3036": ("clic", {"ad": 40, "crit": 25}),
    "3004": ("clic", {"ad": 35}),
    "3042": ("clic", {"ad": 35}),
    # Passif (magique)
    "1052": ("passif", {"ap": 20}),
    "1082": ("passif", {"ap": 15}),
    "1026": ("passif", {"ap": 45}),
    "1058": ("passif", {"ap": 65}),
    "3089": ("passif", {"ap": 130}),
    "3135": ("passif", {"ap": 95}),
    "6653": ("passif", {"ap": 70, "hp": 300}),
    "3115": ("passif", {"ap": 80}),
    "3041": ("passif", {"ap": 20}),
    "3157": ("passif", {"ap": 105}),
    "6655": ("passif", {"ap": 100}),
    # Tank
    "1028": ("tank", {"hp": 150}),
    "1011": ("tank", {"hp": 350}),
    "3083": ("tank", {"hp": 1000}),
    "3084": ("tank", {"hp": 800}),
    "3068": ("tank", {"hp": 550}),
    "3075": ("tank", {"hp": 500}),
    "3053": ("tank", {"hp": 550}),
    "3065": ("tank", {"hp": 450, "regen": 6}),
    # Support : des investissements (or)
    "3865": ("support", {"goldgen": 1, "hp": 30}),
    "3869": ("support", {"goldgen": 2, "hp": 60}),
    "3190": ("support", {"goldgen": 2, "gold": 10, "hp": 200}),
    "3107": ("support", {"goldgen": 1, "gold": 15, "hp": 200}),
    "3222": ("support", {"goldgen": 3, "gold": 5, "hp": 150}),
    "2065": ("support", {"goldgen": 1, "gold": 25, "hp": 100}),
    "3504": ("support", {"goldgen": 2, "gold": 10, "hp": 150}),
    # Élixirs (temporaires)
    "2138": ("elixir", {"hp": 400}),
    "2139": ("elixir", {"ap": 60}),
    "2140": ("elixir", {"ad": 35, "lifesteal": 8}),
}

#: Les effets programmés (par id).
PASSIVES = {
    "3070": {"label": "Charge : +1 charge par clic, +0,02 dégât par clic par charge (max 360)", "stack": "tear"},
    "3004": {"label": "Charge : se transforme en Muramana à 360 charges", "stack": "tear"},
    "3042": {"label": "Muramana : les charges continuent sans limite (de moins en moins)", "stack": "tear"},
    "3031": {"label": "Perfection : +40 % de dégâts critiques", "critdmg": 40},
    "3153": {"label": "Lame du roi déchu : chaque clic inflige en plus 6 % des PV actuels de l'ennemi", "current_hp_pct": 6},
    "6672": {"label": "Tueur de krakens : un clic sur 3 inflige 80 % de tes dégâts par clic en dégâts bruts", "kraken": True},
    "6676": {"label": "Exécute les ennemis sous 5 % de PV, +15 % d'or", "execute": 5, "gold_pct": 15},
    "3036": {"label": "Salutations de Dominik : +25 % de dégâts sur les monstres épiques", "epic_pct": 25},
    "1082": {"label": "Gloire : +1 charge par ennemi vaincu (max 10), +2 de puissance par charge", "stack": "mejai", "max": 10},
    "3041": {"label": "Gloire : +1 charge par ennemi vaincu, sans limite (de moins en moins), la moitié perdue à chaque recul",
             "stack": "mejai", "max": None},
    "3089": {"label": "+40 % de puissance", "ap_pct": 40},
    "3135": {"label": "Bâton du vide : +60 % de dégâts passifs", "passive_pct": 60},
    "6655": {"label": "Écho de Luden : +20 % de dégâts passifs", "passive_pct": 20},
    "6653": {"label": "Tourment : brûle 1 % des PV max de l'ennemi par seconde (0,3 % sur les épiques), au plus 2 × ta puissance", "burn_maxhp_pct": 1},
    "3115": {"label": "Dent de Nashor : chaque clic inflige aussi 20 % de ta puissance", "onhit_ap": 0.2},
    "3157": {"label": "Stase : une fois par combat, sous 30 % de PV, tu ne subis plus de dégâts pendant 3 s", "zhonya": True},
    "3083": {"label": "Armure de Warmog : rend 3 % des PV max par seconde", "regen_maxhp_pct": 3},
    "3084": {"label": "Cœuracier : +30 PV par monstre vaincu, sans limite", "stack": "heartsteel"},
    "3068": {"label": "Égide solaire : brûle l'ennemi (12 + 1 % de tes PV max par seconde)", "sunfire": True},
    "3075": {"label": "Cotte épineuse : renvoie 25 % des dégâts subis", "thorns": 25},
    "3053": {"label": "Gage de Sterak : +25 % de PV quand tu passes sous 30 %", "sterak": True},
    "3065": {"label": "Visage spirituel : +25 % de soins (régénération et vol de vie)", "heal_pct": 25},
    "3865": {"label": "Quête : après 2 000 PO gagnées, devient Opposition céleste (+2 PO toutes les 10 s)", "stack": "atlas"},
}

#: Ancienne classe de Forge d'Ornn → nouvelle (états de la semaine en cours).
OLD_FORGES = {"forge_tireur": "forge_clic", "forge_assassin": "forge_clic", "forge_combattant": "forge_clic",
              "forge_mage": "forge_passif"}


def curate(items: list[dict]) -> dict:
    """Le catalogue de la Faille à partir des objets importés de Data Dragon :
    seulement les objets de CURATED, avec leurs stats de Faille. Un composant
    absent du catalogue est remplacé par sa propre recette."""
    raw = {o["id"]: o for o in items}

    def recipe_of(item_id: str) -> tuple[list[str], int]:
        """(composants de la Faille, coût de recette) : un composant absent
        est remplacé par sa propre recette (ses composants de la Faille et
        son coût de recette)."""
        components, cost = [], raw[item_id]["recipe"]
        for sub in raw[item_id].get("from") or []:
            if sub in CURATED and sub in raw:
                components.append(sub)
            elif sub in raw:
                components += recipe_of(sub)[0]
        return components, cost

    recipes = {i: recipe_of(i) for i in CURATED if i in raw}
    out = {}
    for item_id, (branch, stats) in CURATED.items():
        it = raw.get(item_id)
        if not it:
            continue
        components = recipes[item_id][0]
        # Le prix total reste celui du jeu : la recette est le reste.
        recipe = max(0, it["cost"] - sum(raw[c]["cost"] for c in components))
        into = [o for o, (subs, _) in recipes.items() if item_id in subs]
        kind = "elixir" if branch == "elixir" else "component" if into or item_id == ATLAS_ID else "legendary"
        out[item_id] = {
            **{k: it[k] for k in ("id", "name", "plain", "cost") if k in it},
            "recipe": recipe,
            "from": components,
            "into": into,
            "stats": dict(stats),
            "branch": branch,
            "kind": kind,
            "cls": BRANCHES.get(branch, "") if kind == "legendary" else "",
            "passive": PASSIVES.get(item_id),
            "buyable": item_id not in ("3042", ATLAS_UPGRADE),
        }
    return out


def catalog_from_ddragon(data: dict) -> list[dict]:
    """Les objets de la Faille d'invocateur, depuis item.json (fr_FR) de Data
    Dragon : nom, prix, recette. curate() en garde ceux de la Faille."""
    items = data.get("data", data)
    out = []
    for item_id, it in items.items():
        if int(item_id) >= 10000 or not (it.get("maps") or {}).get("11"):
            continue
        gold = it.get("gold") or {}
        out.append({
            "id": item_id,
            "name": it.get("name", ""),
            "plain": re.sub(r"<[^>]+>", " ", it.get("plaintext") or "").strip(),
            "cost": int(gold.get("total") or 0),
            "recipe": int(gold.get("base") or 0),
            "from": list(it.get("from") or []),
            "into": list(it.get("into") or []),
            "tags": it.get("tags") or [],
        })
    return sorted(out, key=lambda o: (o["cost"], o["name"]))


# --------------------------------------------------------------------- Augments
CDRAGON_ICON = ("https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/"
                "assets/ux/cherry/augments/icons/{}")

#: code → réglages. tier : silver | gold | prismatic ; kind : forge | build | temp.
AUGMENTS = {
    # Forge d'Ornn : une par classe.
    **{f"forge_{cls.lower()}": {"name": f"Forge d'Ornn : {cls}", "tier": "gold", "kind": "forge", "cls": cls,
                                "text": f"Tes légendaires {cls} ({desc}) peuvent devenir des chefs-d'œuvre d'Ornn.",
                                "icon": "forgedbythemaster_large.png"}
       for cls, desc in FORGE_CLASSES.items()},
    "glass_cannon": {"name": "Canon de verre", "tier": "prismatic", "kind": "build", "icon": "glasscannon_large.png",
                     "text": "+40 % de dégâts, mais tes PV max sont réduits de moitié.", "dmg_pct": 40, "hp_mult": 0.5},
    "goliath": {"name": "Goliath", "tier": "prismatic", "kind": "build", "icon": "goliath_large.png",
                "text": "+35 % de PV max et +15 % de dégâts par clic.", "hp_pct": 35, "ad_pct": 15},
    "giant_slayer": {"name": "Tueur de géants", "tier": "prismatic", "kind": "build", "icon": "giantslayer_large.png",
                     "text": "+35 % de dégâts sur les monstres épiques.", "epic_pct": 35},
    "jeweled": {"name": "Gantelet précieux", "tier": "prismatic", "kind": "build", "icon": "jeweledgauntlet_large.png",
                "text": "+40 % de dégâts critiques.", "critdmg": 40},
    "omni_soul": {"name": "Âmes draconiques", "tier": "prismatic", "kind": "build", "icon": "omnisoul_large.png",
                  "text": "Tu obtiens tout de suite l'âme d'un dragon au hasard.", "soul": True},
    "its_critical": {"name": "État critique", "tier": "gold", "kind": "build", "icon": "itscritical_large.png",
                     "text": "+25 % de chances de coup critique sur tes clics.", "crit": 25},
    "lightning": {"name": "Surcharge", "tier": "gold", "kind": "build", "icon": "lightningstrikes_large.png",
                  "text": "+30 % de dégâts passifs.", "passive_pct": 30},
    "minionmancer": {"name": "Sbiromancie", "tier": "gold", "kind": "build", "icon": "minionmancer_large.png",
                     "text": "Les sbires ont 30 % de PV en moins.", "minion_hp_pct": -30},
    "vampirism": {"name": "Vampirisme", "tier": "gold", "kind": "build", "icon": "vampired_large.png",
                  "text": "Tu te soignes de 10 % de tous tes dégâts (clics et passifs).", "omnivamp": 10},
    "tank_engine": {"name": "Moteur de tank", "tier": "gold", "kind": "build", "icon": "tank_engine_large.png",
                    "text": "+1 % de PV max par monstre vaincu (jusqu'à +50 %).", "stack": "tank_engine"},
    "steel_heart": {"name": "Quête : As de cœur", "tier": "gold", "kind": "build", "icon": "quest_steelyourheart_large.png",
                    "text": "Les charges de Cœuracier comptent double.", "heartsteel_mult": 2},
    "donation": {"name": "Pactole", "tier": "gold", "kind": "build", "icon": "donation_large.png",
                 "text": "+1 % d'or par tranche de 1 000 PO non dépensée (jusqu'à +50 %).", "banker": True},
    "double_strike": {"name": "Coup double", "tier": "silver", "kind": "build", "icon": "adamant_large.png",
                      "text": "25 % de chances qu'un clic frappe deux fois.", "double": 25},
    "executioner": {"name": "Bourreau", "tier": "silver", "kind": "build", "icon": "executioner_large.png",
                    "text": "+20 % de dégâts sur les ennemis sous 50 % de PV.", "below_half_pct": 20},
    "silver_spoon": {"name": "Cuillère en argent", "tier": "silver", "kind": "build", "icon": "silver_spoon.png",
                     "text": "+25 % d'or des sbires.", "gold_pct": 25},
    "stackosaurus": {"name": "Cumulator", "tier": "silver", "kind": "build", "icon": "stackosaurusrex_large.png",
                     "text": "Toutes tes charges d'objets comptent pour 50 % de plus.", "stack_mult": 1.5},
    "stats": {"name": "Stats !", "tier": "silver", "kind": "build", "icon": "stats_large.png",
              "text": "+10 % de dégâts par clic, de puissance et de PV.", "ad_pct": 10, "ap_pct": 10, "hp_pct": 10},
    "stats2": {"name": "Stats, stats !", "tier": "gold", "kind": "build", "icon": "statsonstats_large.png",
               "text": "+20 % de dégâts par clic, de puissance et de PV.", "ad_pct": 20, "ap_pct": 20, "hp_pct": 20},
    "stats3": {"name": "Stats, stats, stats !", "tier": "prismatic", "kind": "build", "icon": "statsonstatsonstats_large.png",
               "text": "+35 % de dégâts par clic, de puissance et de PV.", "ad_pct": 35, "ap_pct": 35, "hp_pct": 35},
    # Temporaires (minutes).
    "hand_of_baron": {"name": "Main du baron", "tier": "prismatic", "kind": "temp", "minutes": 30, "icon": "handofbaron_large.png",
                      "text": "Pendant 30 min : +40 % de dégâts par clic et de puissance.", "ad_pct": 40, "ap_pct": 40},
    "light_em_up": {"name": "Déluge", "tier": "silver", "kind": "temp", "minutes": 15, "icon": "lightemup_large.png",
                    "text": "Pendant 15 min : dégâts passifs ×2.", "passive_mult": 2},
    "fire_sale": {"name": "Vente instantanée", "tier": "silver", "kind": "temp", "minutes": 20, "icon": "firesale_large.png",
                  "text": "Pendant 20 min : la boutique coûte 30 % moins cher.", "discount": 30},
    "rags": {"name": "De la misère à la richesse", "tier": "silver", "kind": "temp", "minutes": 20, "icon": "rags_to_riches.png",
             "text": "Pendant 20 min : or des sbires ×2.", "gold_pct": 100},
    "tap_dancer": {"name": "Frénésie", "tier": "gold", "kind": "temp", "minutes": 20, "icon": "tapdancer_large.png",
                   "text": "Pendant 20 min : +25 % de dégâts par clic et +10 % de chances de coup critique.", "ad_pct": 25, "crit": 10},
}
TIER_WEIGHTS = {"silver": 60, "gold": 30, "prismatic": 10}


def augment_icon(code: str) -> str:
    return CDRAGON_ICON.format(AUGMENTS[code]["icon"])


def roll_augments(rng: random.Random, owned: list[str], n: int = 3) -> list[str]:
    """n augments différents à proposer : jamais une Forge déjà obtenue, ni un
    augment définitif déjà pris."""
    pool = [c for c, a in AUGMENTS.items() if not (a["kind"] != "temp" and c in owned)]
    picks: list[str] = []
    while pool and len(picks) < n:
        tier = rng.choices(list(TIER_WEIGHTS), weights=list(TIER_WEIGHTS.values()))[0]
        candidates = [c for c in pool if AUGMENTS[c]["tier"] == tier] or pool
        choice = rng.choice(candidates)
        picks.append(choice)
        pool.remove(choice)
    return picks


# --------------------------------------------------------------------- Stats d'un build
BASE = {"ad": 20.0, "ap": 0.0, "crit": 0.0, "critdmg": 150.0, "hp": 450.0, "regen": 4.0, "lifesteal": 0.0,
        "omnivamp": 0.0, "goldgen": 1.0}
ORNN_MULT = 1.5
MAX_CLICKS_PER_SECOND = 15
#: Le passage d'un ennemi au suivant (secondes).
TRAVEL_SECONDS = 0.6


def compute_stats(run: dict, catalog: dict, now: float | None = None, temps: bool = True) -> dict:
    """Les stats d'un build : base + objets (×1,5 en chef-d'œuvre) + charges +
    drakes + augments. Même calcul que computeStats() côté navigateur (qui y
    ajoute les buffs de monstres).

    run : {inv: [{id, ornn}], stacks: {}, drakes: [], soul, augments: [], temp: [{code, until}]}"""
    s = dict(BASE)
    pct = {"ad": 0.0, "ap": 0.0, "hp": 0.0, "dmg": 0.0, "gold": 0.0, "passive": 0.0}
    flags: dict = {}
    stacks = run.get("stacks") or {}
    stack_mult = heart_mult = passive_mult = 1.0
    active = list(run.get("augments") or [])
    if temps and now is not None:
        active += [t["code"] for t in run.get("temp") or [] if t.get("until", 0) > now]
    for code in active:
        a = AUGMENTS.get(code, {})
        stack_mult *= a.get("stack_mult", 1.0)
        heart_mult *= a.get("heartsteel_mult", 1.0)
    for slot in run.get("inv") or []:
        item = catalog.get(slot["id"])
        if not item:
            continue
        mult = ORNN_MULT if slot.get("ornn") else 1.0
        for k, v in item["stats"].items():
            if k == "gold":
                pct["gold"] += v * mult
            else:
                s[k] = s.get(k, 0) + v * mult
        p = item.get("passive") or {}
        if p.get("critdmg"):
            s["critdmg"] += p["critdmg"]
        for key, target in (("ap_pct", "ap"), ("gold_pct", "gold"), ("passive_pct", "passive")):
            if p.get(key):
                pct[target] += p[key]
        for k in ("execute", "current_hp_pct", "kraken", "onhit_ap", "burn_maxhp_pct", "sunfire", "thorns",
                  "regen_maxhp_pct", "sterak", "epic_pct", "zhonya", "heal_pct"):
            if p.get(k):
                flags[k] = max(flags.get(k, 0), p[k]) if not isinstance(p[k], bool) else True
        stack = p.get("stack")
        if stack == "heartsteel":
            s["hp"] += 30 * stacks.get("heartsteel", 0) * stack_mult * heart_mult
        elif stack == "tear":
            charges = stacks.get("tear", 0) * stack_mult
            if slot["id"] == "3042":
                s["ad"] += 7.2 + 0.4 * math.sqrt(max(0.0, charges - 360))
            else:
                s["ad"] += 0.02 * min(charges, 360)
        elif stack == "mejai":
            cap = p.get("max")
            charges = stacks.get("mejai", 0) * stack_mult
            s["ap"] += 2 * min(charges, cap) if cap else 4 * math.sqrt(charges)
    for x in run.get("elixirs") or []:
        item = catalog.get(x["id"])
        if item and (now is None or x.get("until", 0) > now):
            for k, v in item["stats"].items():
                s[k] = s.get(k, 0) + v
    for kind in run.get("drakes") or []:
        buff = DRAKE_BUFFS.get(kind, {})
        mult = 2 if run.get("soul") == kind else 1
        for key in ("ad", "ap", "hp", "passive"):
            pct[key] += buff.get(f"{key}_pct", 0) * mult
        s["regen"] += buff.get("regen_flat", 0) * mult
        s["crit"] += buff.get("crit", 0) * mult
        if buff.get("low_hp_dmg"):
            flags["low_hp_dmg"] = flags.get("low_hp_dmg", 0) + buff["low_hp_dmg"] * mult
    if run.get("grubs"):
        pct["dmg"] += 2 * run["grubs"]
    hp_mult = 1.0
    for code in active:
        a = AUGMENTS.get(code, {})
        for key in ("ad", "ap", "hp", "dmg", "gold", "passive"):
            pct[key] += a.get(f"{key}_pct", 0)
        s["crit"] += a.get("crit", 0)
        s["critdmg"] += a.get("critdmg", 0)
        s["omnivamp"] += a.get("omnivamp", 0)
        hp_mult *= a.get("hp_mult", 1.0)
        passive_mult *= a.get("passive_mult", 1.0)
        for k in ("epic_pct", "below_half_pct", "double", "minion_hp_pct", "discount"):
            if a.get(k):
                flags[k] = flags.get(k, 0) + a[k]
        if a.get("banker"):
            flags["banker"] = True
        if a.get("stack") == "tank_engine":
            pct["hp"] += min(50, stacks.get("tank_engine", 0) * stack_mult)
    s["ad"] *= 1 + pct["ad"] / 100
    s["ap"] *= 1 + pct["ap"] / 100
    s["hp"] *= (1 + pct["hp"] / 100) * hp_mult
    s["crit"] = min(100.0, s["crit"])
    s["passive_pct"] = pct["passive"]
    s["passive_mult"] = passive_mult
    s["passive_dps"] = s["ap"] * AP_DPS * (1 + pct["passive"] / 100) * passive_mult
    s["dmg_pct"] = pct["dmg"]
    s["gold_pct"] = pct["gold"]
    s["flags"] = flags
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()}


def max_dps(stats: dict) -> float:
    """Un majorant généreux des dégâts par seconde (vérification des sauvegardes) :
    15 clics/s, tous critiques, plus les dégâts passifs et tous les bonus."""
    flags = stats.get("flags") or {}
    click = stats["ad"] * (stats["critdmg"] / 100 + 0.8) + flags.get("onhit_ap", 0) * stats["ap"]
    passive = stats["passive_dps"] * 2 + 12 + stats["hp"] * 0.01 + stats["hp"] * 0.5
    mult = (1 + stats["dmg_pct"] / 100) * 2.5  # buffs temporaires, exécutions, PV actuels…
    return (click * MAX_CLICKS_PER_SECOND * 2 + passive) * mult + 1.0


# --------------------------------------------------------------------- Succès
#: code → (famille, libellé). 20 succès × 0,05 % = 1 %.
ACHIEVEMENTS = {
    "weeks_1": ("Fidélité", "Jouer une semaine"),
    "weeks_5": ("Fidélité", "Jouer 5 semaines"),
    "weeks_20": ("Fidélité", "Jouer 20 semaines"),
    "kills_1k": ("Massacre", "1 000 ennemis vaincus (toutes semaines)"),
    "kills_10k": ("Massacre", "10 000 ennemis vaincus"),
    "kills_100k": ("Massacre", "100 000 ennemis vaincus"),
    "step_100": ("Profondeur", "Atteindre l'étape 100 dans une semaine"),
    "step_200": ("Profondeur", "Atteindre l'étape 200"),
    "step_300": ("Profondeur", "Atteindre l'étape 300"),
    "baron": ("Épiques", "Vaincre un Baron Nashor"),
    "elder": ("Épiques", "Vaincre un Dragon ancestral"),
    "soul": ("Épiques", "Obtenir l'âme d'un dragon"),
    "errant": ("Épiques", "Vaincre un Nashor errant"),
    "ornn": ("Build", "Premier chef-d'œuvre d'Ornn"),
    "full": ("Build", "Full stuff : 6 chefs-d'œuvre d'Ornn"),
    "streak_50": ("Maîtrise", "50 étapes d'affilée sans recul"),
    "noclick_baron": ("Maîtrise", "Vaincre un Nashor sans cliquer"),
    "six_drakes": ("Maîtrise", "Les 6 drakes différents dans une semaine"),
    "top10": ("Compétition", "Finir une semaine dans le top 10"),
    "first": ("Compétition", "Finir une semaine 1er"),
}
ACHIEVEMENT_BONUS = 0.05  # % par succès

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
    "melee": {"name": "Sbire de mêlée", "hp": 1.0, "dps": 1.0, "dmg": "physical", "armor": 0, "mr": 0,
              "gold": 1.3, "rate": 39, "wiki": ["Order_Minion_Melee_Render.png", "Blue_Melee_MinionSquare.png"]},
    "caster": {"name": "Sbire caster", "hp": 0.8, "dps": 1.2, "dmg": "magic", "armor": 0, "mr": 0,
               "gold": 0.9, "rate": 30, "wiki": ["Order_Minion_Caster_Render.png", "Blue_Caster_MinionSquare.png"]},
    "siege": {"name": "Sbire canon", "hp": 3.0, "dps": 1.5, "dmg": "physical", "armor": 0, "mr": 0,
              "gold": 3.2, "rate": 12, "min_step": 3, "wiki": ["Order_Minion_Siege_Render.png", "Blue_Siege_MinionSquare.png"]},
    "super": {"name": "Super sbire", "hp": 6.0, "dps": 2.5, "dmg": "physical", "armor": 0, "mr": 0,
              "gold": 5, "rate": 3, "min_step": 150, "wiki": ["Order_Minion_Super_Render.png", "Blue_Super_MinionSquare.png"]},
    "gromp": {"min_step": 5, "name": "Gromp", "hp": 2.0, "dps": 1.1, "dmg": "magic", "armor": 20, "mr": 20, "rate": 3,
              "buff": {"label": "Poison de Gromp : +20 % de dégâts magiques", "seconds": 90, "magic_pct": 20},
              "wiki": ["Gromp_Render.png", "GrompSquare.png"]},
    "wolves": {"min_step": 5, "name": "Loups des ombres", "hp": 2.0, "dps": 1.4, "dmg": "physical", "armor": 20, "mr": 10, "rate": 3,
               "buff": {"label": "Meute : +50 % d'or des sbires", "seconds": 90, "gold_pct": 50},
               "wiki": ["Greater_Murk_Wolf_Render.png", "Greater_Murk_WolfSquare.png"]},
    "raptors": {"min_step": 5, "name": "Raptors écarlates", "hp": 2.0, "dps": 1.4, "dmg": "physical", "armor": 20, "mr": 10, "rate": 3,
                "buff": {"label": "Instinct de chasse : +15 % de chances de coup critique", "seconds": 90, "crit": 15},
                "wiki": ["Crimson_Raptor_Render.png", "Crimson_RaptorSquare.png"]},
    "krugs": {"min_step": 5, "name": "Krugs antiques", "hp": 2.5, "dps": 1.3, "dmg": "physical", "armor": 40, "mr": 10, "rate": 3,
              "buff": {"label": "Peau de pierre : +30 % de dégâts d'attaque", "seconds": 60, "ad_pct": 30},
              "wiki": ["Ancient_Krug_Render.png", "Ancient_KrugSquare.png"]},
    "scuttle": {"min_step": 5, "name": "Carapateur", "hp": 1.5, "dps": 0.0, "dmg": "physical", "armor": 20, "mr": 20, "rate": 2,
                "buff": {"label": "Sanctuaire de vitesse : +30 % de vitesse de déplacement", "seconds": 120, "ms_pct": 30},
                "wiki": ["Rift_Scuttler_Render.png", "Rift_ScuttlerSquare.png"]},
    "blue": {"min_step": 5, "name": "Sentinelle bleue", "hp": 3.0, "dps": 1.6, "dmg": "magic", "armor": 30, "mr": 30, "rate": 1,
             "buff": {"label": "Buff bleu : +20 % de vitesse de déplacement", "seconds": 120, "ms_pct": 20},
             "wiki": ["Blue_Sentinel_Render.png", "Blue_SentinelSquare.png"]},
    "red": {"min_step": 5, "name": "Ronceraie rouge", "hp": 3.0, "dps": 1.8, "dmg": "physical", "armor": 30, "mr": 30, "rate": 1,
            "buff": {"label": "Buff rouge : les attaques brûlent (+40 % en dégâts bruts) et soignent", "seconds": 120,
                     "burn_pct": 40, "regen_pct": 100},
            "wiki": ["Red_Brambleback_Render.png", "Red_BramblebackSquare.png"]},
    # Jalons
    "infernal": {"name": "Drake infernal", "hp": 2.0, "dps": 1.0, "dmg": "magic", "armor": 40, "mr": 40, "epic": True,
                 "wiki": ["Infernal_Drake_Render.png", "Infernal_DrakeSquare.png"]},
    "mountain": {"name": "Drake des montagnes", "hp": 2.0, "dps": 1.0, "dmg": "physical", "armor": 40, "mr": 40, "epic": True,
                 "wiki": ["Mountain_Drake_Render.png", "Mountain_DrakeSquare.png"]},
    "ocean": {"name": "Drake des océans", "hp": 2.0, "dps": 1.0, "dmg": "magic", "armor": 40, "mr": 40, "epic": True,
              "wiki": ["Ocean_Drake_Render.png", "Ocean_DrakeSquare.png"]},
    "cloud": {"name": "Drake des nuages", "hp": 2.0, "dps": 1.0, "dmg": "magic", "armor": 40, "mr": 40, "epic": True,
              "wiki": ["Cloud_Drake_Render.png", "Cloud_DrakeSquare.png"]},
    "hextech": {"name": "Drake hextech", "hp": 2.0, "dps": 1.0, "dmg": "magic", "armor": 40, "mr": 40, "epic": True,
                "wiki": ["Hextech_Drake_Render.png", "Hextech_DrakeSquare.png"]},
    "chemtech": {"name": "Drake techno-chimique", "hp": 2.0, "dps": 1.0, "dmg": "magic", "armor": 40, "mr": 40, "epic": True,
                 "wiki": ["Chemtech_Drake_Render.png", "Chemtech_DrakeSquare.png"]},
    "herald": {"name": "Héraut de la Faille", "hp": 2.5, "dps": 1.1, "dmg": "physical", "armor": 50, "mr": 50, "epic": True,
               "buff": {"label": "Œil du Héraut : or ×2", "seconds": 180, "gold_pct": 100},
               "wiki": ["Rift_Herald_Render.png", "Rift_HeraldSquare.png"]},
    "grubs": {"name": "Larves du Néant", "hp": 2.5, "dps": 1.1, "dmg": "magic", "armor": 50, "mr": 50, "epic": True,
              "wiki": ["Voidgrub_Render.png", "VoidgrubSquare.png"]},
    "baron": {"name": "Baron Nashor", "hp": 3.0, "dps": 1.2, "dmg": "physical", "armor": 60, "mr": 60, "epic": True,
              "buff": {"label": "Main du Baron : +40 % de dégâts d'attaque et de puissance", "seconds": 180,
                       "ad_pct": 40, "ap_pct": 40},
              "wiki": ["Baron_Nashor_Render.png", "Baron_Nashor_Render_old.png", "Baron_NashorSquare.png"]},
    "elder": {"name": "Dragon ancestral", "hp": 3.0, "dps": 1.2, "dmg": "magic", "armor": 60, "mr": 60, "epic": True,
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
    "mountain": {"label": "+8 % d'armure et de résistance magique", "armor_pct": 8, "mr_pct": 8},
    "ocean": {"label": "+3 PV rendus par seconde", "regen_flat": 3},
    "cloud": {"label": "+7 % de vitesse de déplacement", "ms_pct": 7},
    "hextech": {"label": "+9 % de vitesse d'attaque", "as": 9},
    "chemtech": {"label": "jusqu'à +10 % de dégâts quand tes PV baissent", "low_hp_dmg": 10},
}

#: Croissance par étape (calée par le simulateur, voir README) :
#: PV = coef × (HP_BASE + HP_C × étape^HP_P) ; dégâts = coef × (DPS_BASE + DPS_PER_STEP × étape).
HP_BASE = 40.0
HP_C = 0.08
HP_P = 2.48
DPS_BASE = 4.0
DPS_PER_STEP = 0.08
ARMOR_PER_STEP = 0.6
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
        "armor": round(spec["armor"] + (ARMOR_PER_STEP * step if spec.get("epic") or spec["armor"] else 0.25 * step)),
        "mr": round(spec["mr"] + (ARMOR_PER_STEP * step if spec.get("epic") or spec["mr"] else 0.25 * step)),
        "gold": round(spec.get("gold", 0) * (1 + GOLD_PER_STEP * step), 1),
    }


def sequence(seed: int, start: int, count: int, elder: bool = False) -> list[dict]:
    return [enemy_at(seed, s, elder) for s in range(max(0, start), max(0, start) + count)]


# --------------------------------------------------------------------- Objets (Data Dragon)
#: Libellé de stat dans la description française de Data Dragon → clé.
STAT_LABELS = {
    "dégâts d'attaque": "ad",
    "puissance": "ap",
    "de vitesse d'attaque": "as",
    "de chances de coup critique": "crit",
    "de dégâts de coup critique": "critdmg",
    "pv": "hp",
    "armure": "armor",
    "résistance magique": "mr",
    "létalité": "lethality",
    "de pénétration d'armure": "armorpen",
    "pénétration magique": "mpen",
    "de pénétration magique": "mpenpct",
    "de vol de vie": "lifesteal",
    "d'omnivampirisme": "omnivamp",
    "de régénération de base des pv": "regen",
    "vitesse de déplacement": "ms",
    "de vitesse de déplacement": "mspct",
    "po toutes les 10 sec": "goldgen",
}
STAT_NAMES = {
    "ad": "dégâts d'attaque", "ap": "puissance", "as": "% de vitesse d'attaque", "crit": "% de chances de coup critique",
    "critdmg": "% de dégâts critiques", "hp": "PV", "armor": "armure", "mr": "résistance magique",
    "lethality": "létalité", "armorpen": "% de pénétration d'armure", "mpen": "pénétration magique",
    "mpenpct": "% de pénétration magique", "lifesteal": "% de vol de vie", "omnivamp": "% d'omnivampirisme",
    "regen": "% de régénération des PV", "ms": "vitesse de déplacement", "mspct": "% de vitesse de déplacement",
    "goldgen": "PO toutes les 10 s",
}
#: Exclus : pas des objets de combat, ou propres à d'autres modes.
#: Atlas (objet de support) : sa description Data Dragon est vide.
MANUAL_STATS = {"3865": {"goldgen": 3.0, "hp": 30.0},
                # Élixirs (temporaires, ELIXIR_MINUTES) : leur description ne donne pas de stats.
                "2138": {"hp": 300.0, "armor": 25.0, "mr": 25.0},
                "2139": {"ap": 60.0, "mpen": 10.0},
                "2140": {"ad": 35.0, "omnivamp": 8.0}}
ELIXIR_MINUTES = 10
#: Chaque élixir acheté dans la semaine coûte 30 % de plus que le précédent.
ELIXIR_PRICE_GROWTH = 1.3
ATLAS_ID, ATLAS_UPGRADE = "3865", "3869"
EXCLUDED_ITEM_IDS = {"3870", "3871", "3876", "3877", "2003", "2031", "2055", "2141", "3340", "3363", "3364", "3513", "2051", "3112", "3177", "3184"}
EXCLUDED_NAME_PARTS = ("du gardien", "bébé ", "balise", "potion", "jus ")
ELIXIR_IDS = {"2138", "2139", "2140"}

#: Les objets au passif programmé dans la Faille (par id Data Dragon).
PASSIVES = {
    "3085": {"label": "Rafales : chaque attaque de base frappe 2 fois de plus à 40 %", "extra_hits": 2, "extra_ratio": 0.4},
    "6676": {"label": "Exécute les ennemis sous 5 % de PV, +25 % d'or", "execute": 5, "gold_pct": 25},
    "3089": {"label": "+35 % de puissance", "ap_pct": 35},
    "3084": {"label": "Cœuracier : +30 PV par monstre vaincu, sans limite", "stack": "heartsteel"},
    "3070": {"label": "Charge : +1 PV max et +0,02 AD par clic (max 360 charges)", "stack": "tear"},
    "3004": {"label": "Charge (Manamune) : se transforme en Muramana à 360 charges", "stack": "tear"},
    "3042": {"label": "Muramana : les charges continuent sans limite (de moins en moins)", "stack": "tear"},
    "1082": {"label": "Gloire : +1 charge par ennemi vaincu (max 10), +2 de puissance par charge", "stack": "mejai", "max": 10},
    "3041": {"label": "Gloire : +1 charge par ennemi vaincu, sans limite (de moins en moins), la moitié perdue à chaque recul", "stack": "mejai", "max": None},
    "3153": {"label": "Lame du roi déchu : chaque coup inflige 6 % des PV actuels de l'ennemi", "current_hp_pct": 6},
    "6672": {"label": "Tueur de krakens : une attaque sur 3 inflige des dégâts bruts", "kraken": True},
    "3124": {"label": "Lame enragée : +30 % de dégâts des attaques de base", "auto_pct": 30},
    "3115": {"label": "Dent de Nashor : +20 % de la puissance en dégâts magiques par coup", "onhit_ap": 0.2},
    "6653": {"label": "Tourment : brûle 1 % des PV max de l'ennemi par seconde", "burn_maxhp_pct": 1},
    "3068": {"label": "Égide solaire : brûle 12 + 1 % de tes PV max par seconde", "sunfire": True},
    "3075": {"label": "Cotte épineuse : renvoie 25 % des dégâts subis", "thorns": 25},
    "3083": {"label": "Armure de Warmog : +5 % des PV max rendus par seconde", "regen_maxhp_pct": 5},
    "3053": {"label": "Gage de Sterak : +25 % de PV quand tu passes sous 30 %", "sterak": True},
    "3071": {"label": "Couperet noir : réduit l'armure de l'ennemi de 30 %", "shred": 30},
    "3036": {"label": "Salutations de Dominik : +25 % de dégâts sur les monstres épiques", "epic_pct": 25},
    "3135": {"label": "Bâton du vide : +40 % de pénétration magique", "mpenpct": 40},
    "3865": {"label": "Quête : après 2 000 PO gagnées, devient Opposition céleste (+9 PO toutes les 10 s)", "stack": "atlas"},
}

FORGE_CLASSES = {
    "Tireur": "les objets de coup critique et de vitesse d'attaque",
    "Assassin": "les objets de létalité",
    "Combattant": "les objets de dégâts et de PV",
    "Mage": "les objets de puissance",
    "Tank": "les objets de PV, d'armure et de résistance magique",
    "Support": "les objets de support et de génération d'or",
}


def parse_item_stats(description: str) -> dict:
    """Les stats d'un objet, lues dans sa description (« <stats>… »)."""
    stats: dict = {}
    block = re.search(r"<stats>(.*?)</stats>", description or "", re.S)
    if not block:
        return stats
    for value, label in re.findall(r"<attention>\s*\+?([\d.,]+)\s*%?\s*</attention>\s*([^<]+)", block.group(1)):
        key = STAT_LABELS.get(label.strip().lower().replace("\xa0", " "))
        if key:
            stats[key] = stats.get(key, 0) + float(value.replace(",", "."))
    return stats


def item_class(tags: list, stats: dict) -> str:
    tags = set(tags or [])
    if "GoldPer" in tags or "Aura" in tags:
        return "Support"
    if stats.get("ap"):
        return "Mage"
    if stats.get("crit") or ("AttackSpeed" in tags and stats.get("as")):
        return "Tireur"
    if stats.get("lethality") or stats.get("armorpen"):
        return "Assassin"
    if stats.get("ad"):
        return "Combattant"
    if stats.get("hp") or stats.get("armor") or stats.get("mr"):
        return "Tank"
    return "Support"


def catalog_from_ddragon(data: dict) -> list[dict]:
    """Les objets jouables dans la Faille, depuis item.json (fr_FR) de Data Dragon."""
    items = data.get("data", data)
    out = []
    for item_id, it in items.items():
        name = it.get("name", "")
        if int(item_id) >= 10000 or item_id in EXCLUDED_ITEM_IDS:
            continue
        if any(part in name.lower() for part in EXCLUDED_NAME_PARTS):
            continue
        if not (it.get("maps") or {}).get("11") or it.get("requiredChampion") or it.get("requiredAlly"):
            continue
        gold = it.get("gold") or {}
        purchasable = bool(gold.get("purchasable")) and it.get("inStore", True) is not False
        transforms = item_id in ("3042", ATLAS_UPGRADE)  # obtenus par transformation
        if not purchasable and not transforms:
            continue
        tags = it.get("tags") or []
        if "Trinket" in tags or ("Consumable" in tags and item_id not in ELIXIR_IDS):
            continue
        stats = MANUAL_STATS.get(item_id) or parse_item_stats(it.get("description", ""))
        final = not it.get("into")
        kind = ("elixir" if item_id in ELIXIR_IDS else
                "boots" if "Boots" in tags else
                "legendary" if final and (it.get("from") or transforms) else
                "epic" if it.get("from") else "component")
        out.append({
            "id": item_id,
            "name": name,
            "plain": re.sub(r"<[^>]+>", " ", it.get("plaintext") or "").strip(),
            "cost": int(gold.get("total") or 0),
            "recipe": int(gold.get("base") or 0),
            "from": [i for i in (it.get("from") or []) if i in items],
            "into": [i for i in (it.get("into") or []) if i in items],
            "tags": tags,
            "stats": stats,
            "kind": kind,
            "cls": item_class(tags, stats) if kind == "legendary" else "",
            "passive": PASSIVES.get(item_id),
            "buyable": purchasable and not transforms,
        })
    ids = {o["id"] for o in out}
    for o in out:
        o["from"] = [i for i in o["from"] if i in ids]
        o["into"] = [i for i in o["into"] if i in ids]
    return sorted(out, key=lambda o: (o["cost"], o["name"]))


# --------------------------------------------------------------------- Augments
CDRAGON_ICON = ("https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/"
                "assets/ux/cherry/augments/icons/{}")

#: code → réglages. tier : silver | gold | prismatic ; kind : forge | build | temp.
AUGMENTS = {
    # Forge d'Ornn : une par classe.
    **{f"forge_{cls.lower()}": {"name": f"Forge d'Ornn : {cls}", "tier": "gold", "kind": "forge", "cls": cls,
                                "text": f"Tes légendaires de classe {cls} ({desc}) peuvent devenir des chefs-d'œuvre d'Ornn.",
                                "icon": "forgedbythemaster_large.png"}
       for cls, desc in FORGE_CLASSES.items()},
    "glass_cannon": {"name": "Canon de verre", "tier": "prismatic", "kind": "build", "icon": "glasscannon_large.png",
                     "text": "+40 % de dégâts, mais tes PV max sont réduits de moitié.", "dmg_pct": 40, "hp_mult": 0.5},
    "goliath": {"name": "Goliath", "tier": "prismatic", "kind": "build", "icon": "goliath_large.png",
                "text": "+35 % de PV max et +15 % de dégâts d'attaque.", "hp_pct": 35, "ad_pct": 15},
    "giant_slayer": {"name": "Tueur de géants", "tier": "prismatic", "kind": "build", "icon": "giantslayer_large.png",
                     "text": "+35 % de dégâts sur les monstres épiques.", "epic_pct": 35},
    "jeweled": {"name": "Gantelet précieux", "tier": "prismatic", "kind": "build", "icon": "jeweledgauntlet_large.png",
                "text": "Tes dégâts magiques peuvent aussi faire des coups critiques.", "magic_crit": True},
    "omni_soul": {"name": "Âmes draconiques", "tier": "prismatic", "kind": "build", "icon": "omnisoul_large.png",
                  "text": "Tu obtiens tout de suite l'âme d'un dragon au hasard.", "soul": True},
    "its_critical": {"name": "État critique", "tier": "gold", "kind": "build", "icon": "itscritical_large.png",
                     "text": "+25 % de chances de coup critique.", "crit": 25},
    "lightning": {"name": "Véritable vitesse d'attaque", "tier": "gold", "kind": "build", "icon": "lightningstrikes_large.png",
                  "text": "+40 % de vitesse d'attaque.", "as": 40},
    "minionmancer": {"name": "Sbiromancie", "tier": "gold", "kind": "build", "icon": "minionmancer_large.png",
                     "text": "Les sbires ont 30 % de PV en moins.", "minion_hp_pct": -30},
    "vampirism": {"name": "Vampirisme", "tier": "gold", "kind": "build", "icon": "vampired_large.png",
                  "text": "+15 % d'omnivampirisme.", "omnivamp": 15},
    "tank_engine": {"name": "Moteur de tank", "tier": "gold", "kind": "build", "icon": "tank_engine_large.png",
                    "text": "+1 % de PV max par monstre vaincu (jusqu'à +50 %).", "stack": "tank_engine"},
    "steel_heart": {"name": "Quête : As de cœur", "tier": "gold", "kind": "build", "icon": "quest_steelyourheart_large.png",
                    "text": "Les charges de Cœuracier comptent double.", "heartsteel_mult": 2},
    "donation": {"name": "Pactole", "tier": "gold", "kind": "build", "icon": "donation_large.png",
                 "text": "+1 % d'or par tranche de 1 000 PO non dépensée (jusqu'à +50 %).", "banker": True},
    "double_strike": {"name": "Coup double", "tier": "silver", "kind": "build", "icon": "adamant_large.png",
                      "text": "25 % de chances qu'une attaque frappe deux fois.", "double": 25},
    "executioner": {"name": "Bourreau", "tier": "silver", "kind": "build", "icon": "executioner_large.png",
                    "text": "+20 % de dégâts sur les ennemis sous 50 % de PV.", "below_half_pct": 20},
    "silver_spoon": {"name": "Cuillère en argent", "tier": "silver", "kind": "build", "icon": "silver_spoon.png",
                     "text": "+25 % d'or des sbires.", "gold_pct": 25},
    "stackosaurus": {"name": "Cumulator", "tier": "silver", "kind": "build", "icon": "stackosaurusrex_large.png",
                     "text": "Toutes tes charges d'objets comptent pour 50 % de plus.", "stack_mult": 1.5},
    "stats": {"name": "Stats !", "tier": "silver", "kind": "build", "icon": "stats_large.png",
              "text": "+10 % de dégâts d'attaque, de puissance et de PV.", "ad_pct": 10, "ap_pct": 10, "hp_pct": 10},
    "stats2": {"name": "Stats, stats !", "tier": "gold", "kind": "build", "icon": "statsonstats_large.png",
               "text": "+20 % de dégâts d'attaque, de puissance et de PV.", "ad_pct": 20, "ap_pct": 20, "hp_pct": 20},
    "stats3": {"name": "Stats, stats, stats !", "tier": "prismatic", "kind": "build", "icon": "statsonstatsonstats_large.png",
               "text": "+35 % de dégâts d'attaque, de puissance et de PV.", "ad_pct": 35, "ap_pct": 35, "hp_pct": 35},
    # Temporaires (minutes).
    "hand_of_baron": {"name": "Main du baron", "tier": "prismatic", "kind": "temp", "minutes": 30, "icon": "handofbaron_large.png",
                      "text": "Pendant 30 min : +40 % de dégâts d'attaque et de puissance.", "ad_pct": 40, "ap_pct": 40},
    "light_em_up": {"name": "Déluge d'attaques", "tier": "silver", "kind": "temp", "minutes": 15, "icon": "lightemup_large.png",
                    "text": "Pendant 15 min : vitesse d'attaque ×2.", "as_mult": 2},
    "fire_sale": {"name": "Vente instantanée", "tier": "silver", "kind": "temp", "minutes": 20, "icon": "firesale_large.png",
                  "text": "Pendant 20 min : la boutique coûte 30 % moins cher.", "discount": 30},
    "rags": {"name": "De la misère à la richesse", "tier": "silver", "kind": "temp", "minutes": 20, "icon": "rags_to_riches.png",
             "text": "Pendant 20 min : or des sbires ×2.", "gold_pct": 100},
    "tap_dancer": {"name": "Danse effrénée", "tier": "gold", "kind": "temp", "minutes": 20, "icon": "tapdancer_large.png",
                   "text": "Pendant 20 min : +40 % de vitesse de déplacement et +25 % de vitesse d'attaque.", "ms_pct": 40, "as": 25},
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
BASE = {"ad": 20.0, "ap": 0.0, "as": 0.0, "crit": 0.0, "critdmg": 175.0, "hp": 450.0, "armor": 25.0, "mr": 25.0,
        "lethality": 0.0, "armorpen": 0.0, "mpen": 0.0, "mpenpct": 0.0, "lifesteal": 0.0, "omnivamp": 0.0,
        "regen": 0.0, "regen_flat": 4.0, "ms": 345.0, "mspct": 0.0, "goldgen": 1.0}
ORNN_MULT = 1.5
MAX_CLICKS_PER_SECOND = 15
#: Attaques de base par seconde = vitesse d'attaque (%) / 100 × ceci.
AUTOS_PER_AS = 1.0


def compute_stats(run: dict, catalog: dict, now: float | None = None, temps: bool = True) -> dict:
    """Les stats d'un build : base + objets (×1,5 en chef-d'œuvre) + charges +
    drakes + augments. Même calcul que computeStats() côté navigateur.

    run : {inv: [{id, ornn}], stacks: {}, drakes: [], soul, augments: [], temp: [{code, until}]}"""
    s = dict(BASE)
    pct = {"ad": 0.0, "ap": 0.0, "hp": 0.0, "armor": 0.0, "mr": 0.0, "dmg": 0.0, "gold": 0.0}
    flags: dict = {}
    stacks = run.get("stacks") or {}
    stack_mult = heart_mult = 1.0
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
            s[k] = s.get(k, 0) + v * mult
        p = item.get("passive") or {}
        for k in ("critdmg", "mpenpct"):
            if p.get(k):
                s[k] += p[k]
        if p.get("ap_pct"):
            pct["ap"] += p["ap_pct"]
        if p.get("gold_pct"):
            pct["gold"] += p["gold_pct"]
        for k in ("execute", "extra_hits", "extra_ratio", "current_hp_pct", "kraken", "auto_pct", "onhit_ap",
                  "burn_maxhp_pct", "sunfire", "thorns", "regen_maxhp_pct", "sterak", "shred", "epic_pct"):
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
                s["hp"] += min(charges, 360)
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
        pct["ad"] += buff.get("ad_pct", 0) * mult
        pct["ap"] += buff.get("ap_pct", 0) * mult
        pct["armor"] += buff.get("armor_pct", 0) * mult
        pct["mr"] += buff.get("armor_pct", 0) * mult
        s["regen_flat"] += buff.get("regen_flat", 0) * mult
        s["mspct"] += buff.get("ms_pct", 0) * mult
        s["as"] += buff.get("as", 0) * mult
        if buff.get("low_hp_dmg"):
            flags["low_hp_dmg"] = flags.get("low_hp_dmg", 0) + buff["low_hp_dmg"] * mult
    if run.get("grubs"):
        pct["dmg"] += 2 * run["grubs"]
    hp_mult = 1.0
    for code in active:
        a = AUGMENTS.get(code, {})
        pct["ad"] += a.get("ad_pct", 0)
        pct["ap"] += a.get("ap_pct", 0)
        pct["hp"] += a.get("hp_pct", 0)
        pct["dmg"] += a.get("dmg_pct", 0)
        pct["gold"] += a.get("gold_pct", 0)
        s["crit"] += a.get("crit", 0)
        s["as"] += a.get("as", 0)
        s["omnivamp"] += a.get("omnivamp", 0)
        s["mspct"] += a.get("ms_pct", 0)
        hp_mult *= a.get("hp_mult", 1.0)
        for k in ("epic_pct", "below_half_pct", "double", "minion_hp_pct", "discount"):
            if a.get(k):
                flags[k] = flags.get(k, 0) + a[k]
        for k in ("magic_crit", "banker"):
            if a.get(k):
                flags[k] = True
        if a.get("as_mult"):
            flags["as_mult"] = flags.get("as_mult", 1) * a["as_mult"]
        if a.get("stack") == "tank_engine":
            pct["hp"] += min(50, stacks.get("tank_engine", 0) * stack_mult)
    s["ad"] *= 1 + pct["ad"] / 100
    s["ap"] *= 1 + pct["ap"] / 100
    s["hp"] *= (1 + pct["hp"] / 100) * hp_mult
    s["armor"] *= 1 + pct["armor"] / 100
    s["mr"] *= 1 + pct["mr"] / 100
    s["crit"] = min(100.0, s["crit"])
    s["ms_total"] = (s["ms"]) * (1 + s["mspct"] / 100)
    s["autos"] = s["as"] / 100 * AUTOS_PER_AS * (flags.get("as_mult") or 1)
    s["dmg_pct"] = pct["dmg"]
    s["gold_pct"] = pct["gold"]
    s["flags"] = flags
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()}


def max_dps(stats: dict, armor: float = 0.0) -> float:
    """Un majorant généreux des dégâts par seconde (vérification des sauvegardes) :
    15 clics/s, tous les coups critiques, tous les bonus d'objets."""
    flags = stats.get("flags") or {}
    hit = stats["ad"] * stats["critdmg"] / 100 + 0.3 * stats["ap"] + flags.get("onhit_ap", 0) * stats["ap"]
    hits = MAX_CLICKS_PER_SECOND + stats["autos"] * (1 + flags.get("extra_hits", 0))
    mult = (1 + stats["dmg_pct"] / 100) * 2.5  # buffs temporaires, exécutions, PV actuels…
    return hit * hits * mult + 1.0


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

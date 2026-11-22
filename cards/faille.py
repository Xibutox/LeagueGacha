"""La Faille : le clicker roguelite de la semaine (voir docs et faille_rules.py).

Le combat tourne dans le navigateur (static/js/faille-engine.js). Le
serveur garde l'état de la partie (or, objets, augments…), reçoit une
sauvegarde toutes les 30 s, vérifie qu'elle est possible, puis applique les
récompenses. Les achats, la Forge d'Ornn et le choix des augments passent
par lui.
"""
from __future__ import annotations

import logging
import math
import random
import time

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum

from esport.storage import game_storage

from . import faille_rules as R
from .models import FailleAchievement, FailleData, FailleRun

logger = logging.getLogger(__name__)

START_GOLD = 500.0
MAX_SLOTS = 6
SELL_RATE = 0.7
ORNN_COST = 0.5
#: Hors ligne : l'or de la génération d'or, à ce rendement, plafonné à 12 h.
OFFLINE_RATE = 0.1
OFFLINE_CAP = 12 * 3600
OFFLINE_MIN = 300
#: Une sauvegarde couvre au plus ce temps de jeu (le navigateur sauvegarde toutes les 30 s).
MAX_SYNC_SECONDS = 600
#: Marge de la vérification des dégâts.
DPS_MARGIN = 1.3
#: Les buffs temporaires (camps, Héraut, augments) peuvent au plus multiplier l'or par ceci.
GOLD_BUFF_ALLOWANCE = 3.0
ENEMY_WINDOW = 60
TEAR_IDS = {"3070", "3004", "3042"}
MEJAI_IDS = {"1082", "3041"}
HEARTSTEEL_ID = "3084"
MANAMUNE_ID, MURAMANA_ID = "3004", "3042"
ATLAS_QUEST_GOLD = 2000


class FailleError(Exception):
    pass


# --------------------------------------------------------------------- Données
def _data(key: str) -> FailleData | None:
    return FailleData.objects.filter(key=key).first()


def catalog() -> dict:
    """{id: objet} avec l'URL de son icône (gardé en cache)."""
    cached = cache.get("faille:catalog")
    if cached is not None:
        return cached
    row = _data("items")
    images = (_data("images") or FailleData()).data or {}
    items = {}
    for item in (row.data.get("items", []) if row else []):
        item = dict(item)
        item["passive"] = R.PASSIVES.get(item["id"])
        local = images.get(f"item:{item['id']}")
        item["icon"] = (game_storage().url(local) if local else
                        f"https://ddragon.leagueoflegends.com/cdn/{row.version}/img/item/{item['id']}.png")
        items[item["id"]] = item
    cache.set("faille:catalog", items, 3600)
    return items


def enemy_images() -> dict:
    images = (_data("images") or FailleData()).data or {}
    return {kind: game_storage().url(path) for key, path in images.items()
            if key.startswith("enemy:") and (kind := key[6:])}


def augment_icons() -> dict:
    images = (_data("images") or FailleData()).data or {}
    return {code: (game_storage().url(images[f"augment:{code}"]) if f"augment:{code}" in images else R.augment_icon(code))
            for code in R.AUGMENTS}


def is_ready() -> bool:
    return bool(catalog())


# --------------------------------------------------------------------- Import (Data Dragon, wiki)
WIKI_API = "https://wiki.leagueoflegends.com/en-us/api.php"
DDRAGON = "https://ddragon.leagueoflegends.com"


def sync(images: bool = True, log=None) -> dict:
    """Le catalogue d'objets (Data Dragon, en français) et les images
    (objets, sbires et monstres du LoL Wiki, icônes d'augments de Community Dragon)."""
    log = log or (lambda message: None)
    session = requests.Session()
    session.headers["User-Agent"] = settings.LEAGUEPEDIA_USER_AGENT
    version = session.get(f"{DDRAGON}/api/versions.json", timeout=30).json()[0]
    raw = session.get(f"{DDRAGON}/cdn/{version}/data/fr_FR/item.json", timeout=60).json()
    items = R.catalog_from_ddragon(raw)
    FailleData.objects.update_or_create(key="items", defaults={"version": version, "data": {"items": items}})
    log(f"Data Dragon {version} : {len(items)} objets")
    result = {"version": version, "items": len(items), "images": 0}
    if images:
        result["images"] = _download_images(session, version, items, log)
    cache.delete("faille:catalog")
    return result


def _download_images(session, version: str, items: list[dict], log) -> int:
    storage = game_storage()
    row, _ = FailleData.objects.get_or_create(key="images")
    paths = dict(row.data or {})
    done = 0

    def save(key: str, path: str, url: str) -> bool:
        nonlocal done
        if key in paths and storage.exists(paths[key]):
            return True
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            log(f"! {key} : {exc}")
            return False
        if storage.exists(path):
            storage.delete(path)
        paths[key] = storage.save(path, ContentFile(response.content))
        done += 1
        return True

    for item in items:
        save(f"item:{item['id']}", f"faille/items/{item['id']}.png", f"{DDRAGON}/cdn/{version}/img/item/{item['id']}.png")
    for code, aug in R.AUGMENTS.items():
        save(f"augment:{code}", f"faille/augments/{aug['icon']}", R.augment_icon(code))
    # Sbires et monstres : la vignette (300 px) du rendu du LoL Wiki.
    for kind, spec in R.ENEMIES.items():
        key = f"enemy:{kind}"
        if key in paths and storage.exists(paths[key]):
            continue
        for filename in spec["wiki"]:
            try:
                info = session.get(WIKI_API, params={
                    "action": "query", "titles": f"File:{filename}", "prop": "imageinfo",
                    "iiprop": "url", "iiurlwidth": 300, "format": "json"}, timeout=30).json()
            except (requests.RequestException, ValueError) as exc:
                log(f"! {kind} : {exc}")
                continue
            pages = (info.get("query") or {}).get("pages") or {}
            image = next((p["imageinfo"][0] for p in pages.values() if p.get("imageinfo")), None)
            if image and save(key, f"faille/enemies/{kind}.png", image.get("thumburl") or image["url"]):
                break
        else:
            log(f"! {kind} : aucune image trouvée sur le LoL Wiki")
    row.data = paths
    row.save()
    log(f"{done} image(s) téléchargée(s)")
    return done


# --------------------------------------------------------------------- Partie
def week_start():
    from .weekly import week_start as ws

    return ws()


def _new_state() -> dict:
    return {
        "step": 0, "best": 0, "gold": START_GOLD, "inv": [], "stacks": {}, "drakes": [], "soul": None, "grubs": 0,
        "augments": [], "temp": [], "pending": [], "rewards": [], "elixirs": [], "elixir_count": 0,
        "kills_by": {}, "gold_earned": 0.0, "atlas_gold": 0.0, "max_streak": 0, "noclick": False,
        "clicks": 0, "offline": 0, "synced": time.time(),
    }


def get_run(user, create: bool = True) -> FailleRun | None:
    week = week_start()
    run = FailleRun.objects.filter(user=user, week=week).first()
    if run is None and create:
        _award_rankings(week)
        try:
            run = FailleRun.objects.create(user=user, week=week, state=_new_state())
        except IntegrityError:
            run = FailleRun.objects.get(user=user, week=week)
    return run


def credit_offline(run: FailleRun, now: float | None = None) -> float:
    """L'or gagné hors ligne depuis la dernière sauvegarde (aucun ennemi vaincu)."""
    now = now or time.time()
    state = run.state
    gap = now - float(state.get("synced") or now)
    if gap < OFFLINE_MIN:
        return 0.0
    stats = R.compute_stats(state, catalog(), now)
    gold = stats["goldgen"] / 10 * min(gap, OFFLINE_CAP) * OFFLINE_RATE
    state["gold"] = state.get("gold", 0) + gold
    state["offline"] = round(gold)
    state["synced"] = now
    run.save(update_fields=["state"])
    return gold


def enemies(state: dict, week, start: int, count: int = ENEMY_WINDOW) -> list[dict]:
    return R.sequence(R.week_seed(week.isoformat()), start, count, elder=bool(state.get("soul")))


def _owns(state: dict, ids) -> bool:
    return any(slot["id"] in ids for slot in state.get("inv") or [])


def _gold_for(enemy: dict, stats: dict, unspent: float, best: int) -> float:
    if not enemy["gold"]:
        return 0.0
    bonus = stats["gold_pct"]
    if stats["flags"].get("banker"):
        bonus += min(50, int(unspent // 1000))
    gap = max(0, best - enemy["step"] - 1)
    return enemy["gold"] * (1 + bonus / 100) * max(R.FARM_MIN, 1 - R.FARM_PENALTY_PER_STEP * gap)


def apply_sync(run: FailleRun, payload: dict, now: float | None = None) -> dict:
    """Applique une sauvegarde du navigateur, après vérification. Renvoie
    {"ok", "reason"?, "new": [événements]}."""
    now = now or time.time()
    state = run.state
    cat = catalog()
    elapsed = max(0.0, now - float(state.get("synced") or now))
    try:
        duration = max(0.0, min(float(payload.get("duration") or 0), elapsed + 5, MAX_SYNC_SECONDS))
        clicks = max(0, min(int(payload.get("clicks") or 0), int(R.MAX_CLICKS_PER_SECOND * duration) + 1))
        kills = sorted((int(s), int(n)) for s, n in (payload.get("kills") or []) if int(n) > 0 and int(s) >= 0)
        losses = sum(max(0, int(n)) for _, n in (payload.get("losses") or []))
        claim = float(payload.get("gold") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "Sauvegarde illisible."}
    if not kills and not clicks and not losses:
        state["synced"] = now
        run.save(update_fields=["state"])
        return {"ok": True, "new": []}

    stats = R.compute_stats(state, cat, now)
    seed = R.week_seed(run.week.isoformat())
    elder = bool(state.get("soul"))
    best = int(state.get("best", 0))
    old_best = best
    total_hp = base_gold = 0.0
    killed: list[tuple[dict, int]] = []
    for step, count in kills:
        if step > best:
            return {"ok": False, "reason": "Progression impossible : une étape a été sautée."}
        enemy = R.enemy_at(seed, step, elder)
        if step == best:
            best += 1
        killed.append((enemy, count))
        # Châtiment retire 15 % des PV d'un monstre.
        total_hp += enemy["hp"] * count * (1 if enemy["kind"] in R.MINIONS else 0.85)
        base_gold += _gold_for(enemy, stats, state.get("gold", 0), best) * count
    if best - old_best > duration / 0.5 + 1:
        return {"ok": False, "reason": "Progression trop rapide."}
    if total_hp > R.max_dps(stats) * DPS_MARGIN * max(duration, 1) + 500:
        return {"ok": False, "reason": "Dégâts impossibles avec ton build."}

    gold = min(max(0.0, claim), base_gold * GOLD_BUFF_ALLOWANCE) if claim else base_gold
    events: list[dict] = []
    state["gold"] = state.get("gold", 0) + gold
    state["gold_earned"] = state.get("gold_earned", 0) + gold
    if _owns(state, {R.ATLAS_ID}):
        state["atlas_gold"] = state.get("atlas_gold", 0) + gold
    total = sum(n for _, n in kills)
    monsters = sum(n for e, n in killed if e["kind"] not in R.MINIONS)
    kills_by = state.setdefault("kills_by", {})
    for enemy, n in killed:
        kills_by[enemy["kind"]] = kills_by.get(enemy["kind"], 0) + n
    stacks = state.setdefault("stacks", {})
    if _owns(state, TEAR_IDS):
        stacks["tear"] = stacks.get("tear", 0) + clicks
    if _owns(state, {HEARTSTEEL_ID}):
        stacks["heartsteel"] = stacks.get("heartsteel", 0) + monsters
    if _owns(state, MEJAI_IDS):
        stacks["mejai"] = stacks.get("mejai", 0) + total
        for _ in range(min(losses, 20)):
            stacks["mejai"] = stacks["mejai"] // 2
    if "tank_engine" in state.get("augments", []):
        stacks["tank_engine"] = stacks.get("tank_engine", 0) + monsters
    state["clicks"] = state.get("clicks", 0) + clicks

    # Récompenses d'étape : une seule fois chacune.
    rewards = set(state.get("rewards") or [])
    noclick = {int(s) for s in payload.get("noclick") or []}
    for enemy, _ in killed:
        step = enemy["step"]
        if step in rewards:
            continue
        kind = enemy["kind"]
        if kind in R.DRAKES:
            rewards.add(step)
            state.setdefault("drakes", []).append(kind)
            events.append({"type": "drake", "kind": kind})
            if len(state["drakes"]) >= 4 and not state.get("soul"):
                state["soul"] = kind
                events.append({"type": "soul", "kind": kind})
        elif kind == "grubs":
            rewards.add(step)
            state["grubs"] = state.get("grubs", 0) + 1
        elif kind in ("baron", "elder"):
            rewards.add(step)
            rng = random.Random(f"{run.pk}-{step}-{len(state.get('augments', []))}")
            offer = R.roll_augments(rng, state.get("augments", []))
            state.setdefault("pending", []).append(offer)
            counter = "errants" if enemy["errant"] else ("elders" if kind == "elder" else "barons")
            state[counter] = state.get(counter, 0) + 1
            if step in noclick and kind == "baron":
                state["noclick"] = True
            events.append({"type": "augment", "kind": kind, "errant": enemy["errant"]})
    state["rewards"] = sorted(rewards)

    # Transformations : Manamune → Muramana, Atlas → Opposition céleste.
    for slot in state.get("inv") or []:
        if slot["id"] == MANAMUNE_ID and stacks.get("tear", 0) >= 360 and MURAMANA_ID in cat:
            slot["id"] = MURAMANA_ID
            events.append({"type": "transform", "item": MURAMANA_ID})
        if slot["id"] == R.ATLAS_ID and state.get("atlas_gold", 0) >= ATLAS_QUEST_GOLD and R.ATLAS_UPGRADE in cat:
            slot["id"] = R.ATLAS_UPGRADE
            events.append({"type": "transform", "item": R.ATLAS_UPGRADE})

    state["best"] = best
    state["step"] = max(0, min(int(payload.get("step") or 0), best))
    try:
        streak = int(payload.get("streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    state["max_streak"] = max(state.get("max_streak", 0), min(streak, total))
    state["temp"] = [t for t in state.get("temp") or [] if t["until"] > now]
    state["elixirs"] = [x for x in state.get("elixirs") or [] if x["until"] > now]
    state["synced"] = now
    run.kills += total
    run.best = max(run.best, best)
    run.state = state
    from django.utils import timezone

    run.synced_at = timezone.now()
    run.save(update_fields=["state", "kills", "best", "synced_at"])
    events += [{"type": "achievement", "code": c} for c in check_achievements(run)]
    return {"ok": True, "new": events, "gold": round(gold, 1)}


# --------------------------------------------------------------------- Boutique
def purchase_cost(item_id: str, inv: list[dict], cat: dict) -> tuple[int, list[int]]:
    """Le prix d'un objet avec les composants déjà possédés (comme en jeu)."""
    used: set[int] = set()

    def cost(iid: str) -> int:
        item = cat[iid]
        total = item["recipe"]
        for sub in item.get("from") or []:
            index = next((i for i, slot in enumerate(inv) if i not in used and slot["id"] == sub and not slot.get("ornn")), None)
            if index is not None:
                used.add(index)
            else:
                total += cost(sub)
        return total

    return cost(item_id), sorted(used)


def _discount(state: dict, now: float) -> float:
    stats = R.compute_stats(state, catalog(), now)
    return min(90, stats["flags"].get("discount", 0)) / 100


def buy(run: FailleRun, item_id: str, now: float | None = None) -> dict:
    now = now or time.time()
    cat = catalog()
    item = cat.get(str(item_id))
    if not item or not item.get("buyable") or item["kind"] == "elixir":
        raise FailleError("Cet objet n'est pas en vente.")
    state = run.state
    if item["kind"] in ("legendary", "boots") and any(s["id"] == item["id"] for s in state["inv"]):
        raise FailleError("Tu as déjà cet objet.")
    cost, used = purchase_cost(item["id"], state["inv"], cat)
    cost = round(cost * (1 - _discount(state, now)))
    if len(state["inv"]) - len(used) + 1 > MAX_SLOTS:
        raise FailleError("Inventaire plein : 6 emplacements. Vends un objet d'abord.")
    if state["gold"] < cost:
        raise FailleError(f"Il te manque {math.ceil(cost - state['gold'])} PO.")
    state["gold"] -= cost
    state["inv"] = [s for i, s in enumerate(state["inv"]) if i not in used] + [{"id": item["id"], "ornn": False}]
    if item["id"] == R.ATLAS_ID:
        state["atlas_gold"] = 0
    run.save(update_fields=["state"])
    return {"cost": cost}


def sell(run: FailleRun, index: int) -> dict:
    state = run.state
    try:
        slot = state["inv"][int(index)]
    except (IndexError, ValueError, TypeError):
        raise FailleError("Emplacement vide.")
    item = catalog().get(slot["id"]) or {"cost": 0}
    refund = round(item["cost"] * SELL_RATE * (1 + (ORNN_COST if slot.get("ornn") else 0)))
    state["gold"] += refund
    del state["inv"][int(index)]
    run.save(update_fields=["state"])
    return {"refund": refund}


def ornn(run: FailleRun, index: int) -> dict:
    state = run.state
    try:
        slot = state["inv"][int(index)]
    except (IndexError, ValueError, TypeError):
        raise FailleError("Emplacement vide.")
    item = catalog().get(slot["id"])
    if not item or item["kind"] != "legendary":
        raise FailleError("Seul un objet légendaire peut devenir un chef-d'œuvre d'Ornn.")
    if slot.get("ornn"):
        raise FailleError("C'est déjà un chef-d'œuvre.")
    if f"forge_{item['cls'].lower()}" not in state.get("augments", []):
        raise FailleError(f"Il faut l'augment Forge d'Ornn : {item['cls']}.")
    cost = round(item["cost"] * ORNN_COST)
    if state["gold"] < cost:
        raise FailleError(f"Il te manque {math.ceil(cost - state['gold'])} PO.")
    state["gold"] -= cost
    slot["ornn"] = True
    run.save(update_fields=["state"])
    events = check_achievements(run)
    return {"cost": cost, "achievements": events}


def elixir_price(state: dict) -> int:
    return round(500 * R.ELIXIR_PRICE_GROWTH ** state.get("elixir_count", 0))


def buy_elixir(run: FailleRun, item_id: str, now: float | None = None) -> dict:
    now = now or time.time()
    item = catalog().get(str(item_id))
    if not item or item["kind"] != "elixir":
        raise FailleError("Ce n'est pas un élixir.")
    state = run.state
    price = elixir_price(state)
    if state["gold"] < price:
        raise FailleError(f"Il te manque {math.ceil(price - state['gold'])} PO.")
    state["gold"] -= price
    state["elixir_count"] = state.get("elixir_count", 0) + 1
    elixirs = [x for x in state.get("elixirs") or [] if x["until"] > now and x["id"] != item["id"]]
    elixirs.append({"id": item["id"], "until": now + R.ELIXIR_MINUTES * 60})
    state["elixirs"] = elixirs
    run.save(update_fields=["state"])
    return {"cost": price}


def choose_augment(run: FailleRun, code: str, now: float | None = None) -> dict:
    now = now or time.time()
    state = run.state
    pending = state.get("pending") or []
    if not pending or code not in pending[0]:
        raise FailleError("Cet augment ne t'est pas proposé.")
    augment = R.AUGMENTS[code]
    if augment["kind"] == "temp":
        state.setdefault("temp", []).append({"code": code, "until": now + augment["minutes"] * 60})
    else:
        state.setdefault("augments", []).append(code)
        if augment.get("soul") and not state.get("soul"):
            state["soul"] = random.choice(R.DRAKES)
    state["pending"] = pending[1:]
    run.save(update_fields=["state"])
    return {"augment": code}


# --------------------------------------------------------------------- Succès
def achievement_count(user) -> int:
    if user is None or not getattr(user, "is_authenticated", False):
        return 0
    key = f"faille:ach:{user.pk}"
    count = cache.get(key)
    if count is None:
        count = FailleAchievement.objects.filter(user=user).count()
        cache.set(key, count, 3600)
    return count


def achievement_bonus(user) -> float:
    return round(achievement_count(user) * R.ACHIEVEMENT_BONUS, 2)


def _unlock(user, codes: list[str]) -> list[str]:
    have = set(FailleAchievement.objects.filter(user=user).values_list("code", flat=True))
    new = [c for c in codes if c in R.ACHIEVEMENTS and c not in have]
    for code in new:
        FailleAchievement.objects.get_or_create(user=user, code=code)
    if new:
        cache.delete(f"faille:ach:{user.pk}")
        from .services import refresh_rosters_of_user

        transaction.on_commit(lambda: refresh_rosters_of_user(user))
    return new


def check_achievements(run: FailleRun) -> list[str]:
    state = run.state
    user = run.user
    codes = []
    runs = FailleRun.objects.filter(user=user, kills__gt=0)
    weeks = runs.count()
    total = runs.aggregate(n=Sum("kills"))["n"] or 0
    for n, code in ((1, "weeks_1"), (5, "weeks_5"), (20, "weeks_20")):
        if weeks >= n:
            codes.append(code)
    for n, code in ((1000, "kills_1k"), (10_000, "kills_10k"), (100_000, "kills_100k")):
        if total >= n:
            codes.append(code)
    for n, code in ((100, "step_100"), (200, "step_200"), (300, "step_300")):
        if state.get("best", 0) >= n:
            codes.append(code)
    if state.get("barons"):
        codes.append("baron")
    if state.get("elders"):
        codes.append("elder")
    if state.get("errants"):
        codes.append("errant")
    if state.get("soul"):
        codes.append("soul")
    ornns = [s for s in state.get("inv") or [] if s.get("ornn")]
    if ornns:
        codes.append("ornn")
    if len(ornns) >= 6:
        codes.append("full")
    if state.get("max_streak", 0) >= 50:
        codes.append("streak_50")
    if state.get("noclick"):
        codes.append("noclick_baron")
    if len(set(state.get("drakes") or [])) >= 6:
        codes.append("six_drakes")
    return _unlock(user, codes)


def _award_rankings(week) -> None:
    """Les succès « top 10 » et « 1er » de la semaine précédente, une fois."""
    from datetime import timedelta

    previous = week - timedelta(days=7)
    key = f"awarded:{previous.isoformat()}"
    if FailleData.objects.filter(key=key).exists():
        return
    try:
        FailleData.objects.create(key=key)
    except IntegrityError:
        return
    ranked = list(FailleRun.objects.filter(week=previous, kills__gt=0).order_by("-kills", "best").select_related("user")[:10])
    for rank, run in enumerate(ranked, start=1):
        _unlock(run.user, ["top10"] + (["first"] if rank == 1 else []))


def leaderboard(week=None, limit: int = 100) -> list[tuple[int, FailleRun]]:
    week = week or week_start()
    runs = list(FailleRun.objects.filter(week=week, kills__gt=0).select_related("user").order_by("-kills", "-best")[:limit])
    counts = dict(FailleAchievement.objects.filter(user__in=[r.user_id for r in runs]).order_by()
                  .values("user").annotate(n=Count("id")).values_list("user", "n"))
    for run in runs:
        run.achievements = counts.get(run.user_id, 0)
    return list(enumerate(runs, start=1))


# --------------------------------------------------------------------- Pour la page
def public_state(run: FailleRun) -> dict:
    state = dict(run.state)
    state.pop("rewards", None)
    state["kills"] = run.kills
    state["elixir_price"] = elixir_price(run.state)
    return state


def page_data(run: FailleRun) -> dict:
    from .weekly import week_end

    cat = catalog()
    images = enemy_images()
    icons = augment_icons()
    state = run.state
    start = max(0, int(state.get("step", 0)) - 10)
    have = set(FailleAchievement.objects.filter(user=run.user).values_list("code", flat=True))
    return {
        "now": time.time(),
        "week": run.week.isoformat(),
        "ends": week_end(run.week).timestamp(),
        "state": public_state(run),
        "enemies": enemies(state, run.week, start, int(state.get("best", 0)) - start + ENEMY_WINDOW),
        "enemyInfo": {k: {"name": v["name"], "image": images.get(k, ""), "epic": bool(v.get("epic")),
                          "camp": k in R.CAMPS} for k, v in R.ENEMIES.items()},
        "enemyBuffs": {k: v["buff"] for k, v in R.ENEMIES.items() if v.get("buff")},
        "drakeBuffs": R.DRAKE_BUFFS,
        "catalog": cat,
        "augments": {c: dict(a, icon=icons[c]) for c, a in R.AUGMENTS.items()},
        "statNames": R.STAT_NAMES,
        "forgeClasses": R.FORGE_CLASSES,
        "achievements": [{"code": c, "family": f, "label": l, "done": c in have} for c, (f, l) in R.ACHIEVEMENTS.items()],
        "achievementBonus": R.ACHIEVEMENT_BONUS,
        "rules": {"slots": MAX_SLOTS, "sell": SELL_RATE, "ornn": ORNN_COST, "elixirMinutes": R.ELIXIR_MINUTES,
                  "offlineRate": OFFLINE_RATE, "farmPenalty": R.FARM_PENALTY_PER_STEP, "farmMin": R.FARM_MIN},
    }

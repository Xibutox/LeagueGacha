// Usage : python manage.py sync_faille --regles-sim faille-rules.json, puis
//         RULES=faille-rules.json OFFLINE_RATE=0.1 node tools/faille-sim.js [Profil]
// Simulateur de calibrage de la Faille : joue une semaine pour un profil.
const E = require("../static/js/faille-engine.js");
const R = require(require("path").resolve(process.env.RULES || "faille-rules.json"));

function mulberry(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

const PLANS = {
  clic: ["3031", "6676", "3072", "3004", "3153", "3036"],
  passif: ["3089", "3135", "6653", "3041", "3115", "6655"],
  mixte: ["3031", "3089", "3135", "3072", "3083", "6653"],
  mage: ["3089", "3135", "6653", "3157", "3041", "3083"],
  eco: ["3865", "3190", "3089", "3135", "6653", "3041"],
};
const OFFLINE_CAP = 12 * 3600;
const OFFLINE_RATE = Number(process.env.OFFLINE_RATE || 0.5);

function simulate(profile) {
  const rng = mulberry(profile.seed || 7);
  const cat = R.catalog;
  const run = { inv: [], stacks: {}, drakes: [], soul: null, grubs: 0, augments: [], temp: [], buffs: [] };
  let gold = 500, step = 0, best = 0, kills = 0, now = 0, augOffers = 0, rewards = {};
  const plan = PLANS[profile.plan || process.env.PLAN || "clic"];
  const forgeCls = new Set(plan.map((id) => cat[id] && cat[id].cls).filter(Boolean));
  let fullAt = null;
  const days = 7;
  const log = [];
  for (let day = 0; day < days; day++) {
    // Hors ligne avant la session (le reste de la journée).
    const offline = 86400 - profile.minutes * 60;
    const st0 = E.computeStats(run, cat, R.augments, R.drakeBuffs, R.enemyBuffs, now);
    if (day > 0) gold += st0.goldgen / 10 * Math.min(offline, OFFLINE_CAP) * OFFLINE_RATE;
    now += offline;
    let active = profile.minutes * 60;
    let fight = null, travel = 0, clickAcc = 0, losses = 0, lastLoss = -1, farmUntil = -1, farmStep = 0, invLen = run.inv.length, goldMark = 0;
    const dt = 0.1;
    while (active > 0) {
      active -= dt; now += dt;
      const stats = E.computeStats(run, cat, R.augments, R.drakeBuffs, R.enemyBuffs, now);
      shop(run, stats);
      if (!fight) {
        if (travel > 0) { travel -= dt; continue; }
        const list = run.soul ? R.enemiesElder : R.enemies;
        if (farmUntil > now) {
          // Farm : le sbire le plus haut sous le mur.
          step = farmStep;
        }
        const enemy = list[Math.min(step, list.length - 1)];
        fight = new E.Fight(enemy, stats, { rng, epic: ["baron", "elder", "herald", "grubs"].indexOf(enemy.kind) >= 0 || R.drakeBuffs[enemy.kind] });
      }
      fight.stats = stats;
      clickAcc += profile.cps * dt;
      while (clickAcc >= 1 && !fight.done) { clickAcc -= 1; fight.click(); run.stacks.tear = (run.stacks.tear || 0) + 1; }
      const res = fight.tick(dt);
      if (res === "win") {
        const e = fight.enemy;
        kills++;
        const g = E.goldFor(e, stats, gold, best);
        gold += g;
        if (run.inv.some((s) => s.id === "3865")) run.atlasGold = (run.atlasGold || 0) + g;
        if (!fight.isMinion) {
          run.stacks.heartsteel = (run.stacks.heartsteel || 0) + 1;
          run.stacks.tank_engine = (run.stacks.tank_engine || 0) + 1;
        }
        run.stacks.mejai = (run.stacks.mejai || 0) + 1;
        if (R.enemyBuffs[e.kind]) run.buffs.push({ kind: e.kind, until: now + R.enemyBuffs[e.kind].seconds });
        if (!rewards[step]) {
          rewards[step] = true;
          if (R.drakeBuffs[e.kind]) { run.drakes.push(e.kind); if (run.drakes.length === 4 && !run.soul) run.soul = e.kind; }
          if (e.kind === "grubs") run.grubs++;
          if (e.kind === "baron" || e.kind === "elder") { augOffers++; chooseAugment(run, rng, forgeCls, now); }
        }
        if (!(farmUntil > now)) { step++; best = Math.max(best, step); }
        fight = null; travel = E.travelTime(stats);
      } else if (res === "lose") {
        run.stacks.mejai = Math.floor((run.stacks.mejai || 0) / 2);
        losses = (lastLoss === step) ? losses + 1 : 1; lastLoss = step;
        step = Math.max(0, step - 1);
        if (losses >= 3) {
          const list = run.soul ? R.enemiesElder : R.enemies;
          farmStep = step; while (farmStep > 0 && !list[farmStep].gold) farmStep--;
          farmStep = Math.max(0, farmStep - 3); while (farmStep > 0 && !list[farmStep].gold) farmStep--;
          farmUntil = now + 600; losses = 0;
        }
        fight = null; travel = E.travelTime(stats);
      }
      run.buffs = run.buffs.filter((b) => b.until > now);
      if (farmUntil > now && run.inv.length !== invLen) { farmUntil = -1; step = best - 1; }
      invLen = run.inv.length;
      // Manamune → Muramana
      run.inv.forEach((s) => { if (s.id === "3004" && (run.stacks.tear || 0) >= 360) s.id = "3042"; });
      const gen = stats.goldgen / 10 * dt;
      gold += gen;
      if (run.inv.some((s) => s.id === "3865")) {
        run.atlasGold = (run.atlasGold || 0) + gen;
        if (run.atlasGold >= 2000) run.inv.forEach((s) => { if (s.id === "3865") s.id = "3869"; });
      }
      const masterworks = run.inv.filter((s) => s.ornn).length;
      if (masterworks >= 6 && fullAt === null) fullAt = day + 1;
    }
    log.push({ day: day + 1, best, kills, gold: Math.round(gold), inv: run.inv.map((s) => (cat[s.id] ? cat[s.id].name : s.id) + (s.ornn ? "*" : "")).join(", "), aug: run.augments.join(",") });
  }
  return { log, augOffers, fullAt };

  function shop(run, stats) {
    // Forge d'Ornn dès que possible.
    run.inv.forEach((s) => {
      const it = cat[s.id];
      if (!s.ornn && it.kind === "legendary" && run.augments.indexOf("forge_" + it.cls.toLowerCase()) >= 0 && gold >= it.cost * 0.5) {
        gold -= it.cost * 0.5; s.ornn = true;
      }
    });
    // Prochain objet du plan pas encore possédé.
    const owned = new Set(run.inv.map((s) => (s.id === "3042" ? "3004" : s.id === "3869" ? "3865" : s.id)));
    const target = plan.find((id) => !owned.has(id));
    if (!target) {
      // Plus rien à acheter : un élixir de rage.
      run.elixirs = (run.elixirs || []).filter((x) => x.until > now);
      run.elixirBought = run.elixirBought || 0;
      const price = 500 * Math.pow(1.3, run.elixirBought);
      if (!run.elixirs.length && gold >= price) { gold -= price; run.elixirBought++; run.elixirs.push({ id: "2140", until: now + 600 }); }
      return;
    }
    const p = E.purchase(target, run.inv, cat);
    const slotsAfter = run.inv.length - p.consumed.length + 1;
    if (gold >= p.cost * (1 - discount(stats)) && slotsAfter <= 6) {
      gold -= p.cost * (1 - discount(stats));
      run.inv = run.inv.filter((_, i) => p.consumed.indexOf(i) < 0);
      run.inv.push({ id: target });
      return;
    }
    // Sinon un composant abordable de la cible.
    const comps = (cat[target].from || []).filter((c) => !run.inv.some((s) => s.id === c));
    for (const c of comps) {
      const q = E.purchase(c, run.inv, cat);
      if (gold >= q.cost && run.inv.length - q.consumed.length + 1 <= 6) {
        gold -= q.cost; run.inv = run.inv.filter((_, i) => q.consumed.indexOf(i) < 0); run.inv.push({ id: c }); return;
      }
    }
  }
  function discount(stats) { return (stats.flags.discount || 0) / 100; }
}

function chooseAugment(run, rng, forgeCls, now) {
  const owned = run.augments;
  const pool = Object.keys(R.augments).filter((c) => !(R.augments[c].kind !== "temp" && owned.indexOf(c) >= 0));
  const picks = [];
  while (picks.length < 3 && pool.length) {
    const r = rng() * 100;
    const tier = r < 60 ? "silver" : r < 90 ? "gold" : "prismatic";
    const cands = pool.filter((c) => R.augments[c].tier === tier);
    const list = cands.length ? cands : pool;
    const c = list[Math.floor(rng() * list.length)];
    picks.push(c); pool.splice(pool.indexOf(c), 1);
  }
  const forge = picks.find((c) => R.augments[c].kind === "forge" && forgeCls.has(R.augments[c].cls));
  const choice = forge || picks.find((c) => R.augments[c].kind === "build") || picks[0];
  if (R.augments[choice].kind === "temp") run.temp.push({ code: choice, until: now + R.augments[choice].minutes * 60 });
  else { run.augments.push(choice); if (R.augments[choice].soul && !run.soul) run.soul = "infernal"; }
}

const profiles = [
  { name: "Curieux", minutes: 10, cps: 4 },
  { name: "Régulier", minutes: 60, cps: 4 },
  { name: "Acharné", minutes: 240, cps: 3 },
  { name: "Non-stop", minutes: 600, cps: 2 },
];
const only = process.argv[2];
for (const p of profiles) {
  if (only && p.name !== only) continue;
  for (const seed of [7]) {
    const r = simulate(Object.assign({ seed }, p));
    const last = r.log[r.log.length - 1];
    console.log(`${p.name}: étape ${last.best}, ${last.kills} kills, or ${last.gold}, augments proposés ${r.augOffers}, full ${r.fullAt}`);
    console.log("   ", last.inv, "|", last.aug);
    if (process.env.DAYS) r.log.forEach((l) => console.log("     j" + l.day, l.best, l.kills, l.gold, l.inv));
  }
}

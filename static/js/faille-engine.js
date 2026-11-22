// La Faille : le moteur de combat, sans interface. Utilisé par la page
// (static/js/faille.js) et par le simulateur de calibrage (Node). Les stats
// d'un build sont calculées comme dans cards/faille_rules.py (compute_stats).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.FailleEngine = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var BASE = { ad: 20, ap: 0, as: 0, crit: 0, critdmg: 175, hp: 450, armor: 25, mr: 25, lethality: 0, armorpen: 0,
    mpen: 0, mpenpct: 0, lifesteal: 0, omnivamp: 0, regen: 0, regen_flat: 4, ms: 345, mspct: 0, goldgen: 1 };
  var ORNN_MULT = 1.5;
  var MAX_CPS = 15;
  var AUTOS_PER_AS = 1.0;
  var BASE_MS = 345;
  var TRAVEL_SECONDS = 1.0;
  var SMITE_COOLDOWN = 90;
  var SMITE_PCT = 15;
  // Au-delà de ce temps, l'ennemi s'enrage : ses dégâts augmentent de 12 % par seconde.
  var ENRAGE_AFTER = 20;
  var ENRAGE_AFTER_EPIC = 60;
  var ENRAGE_AFTER_CAMP = 40;
  var ENRAGE_RATE = 1.12;

  function add(obj, key, v) { obj[key] = (obj[key] || 0) + v; }

  // run : {inv: [{id, ornn}], stacks, drakes, soul, grubs, augments, temp: [{code, until}], buffs: [{kind, until}]}
  function computeStats(run, catalog, augments, drakeBuffs, enemyBuffs, now) {
    var s = {};
    Object.keys(BASE).forEach(function (k) { s[k] = BASE[k]; });
    var pct = { ad: 0, ap: 0, hp: 0, armor: 0, mr: 0, dmg: 0, gold: 0, magic: 0 };
    var flags = {};
    var stacks = run.stacks || {};
    var active = (run.augments || []).slice();
    (run.temp || []).forEach(function (t) { if (t.until > now) active.push(t.code); });
    var stackMult = 1, heartMult = 1;
    active.forEach(function (code) {
      var a = augments[code] || {};
      if (a.stack_mult) stackMult *= a.stack_mult;
      if (a.heartsteel_mult) heartMult *= a.heartsteel_mult;
    });
    (run.inv || []).forEach(function (slot) {
      var item = catalog[slot.id];
      if (!item) return;
      var mult = slot.ornn ? ORNN_MULT : 1;
      Object.keys(item.stats).forEach(function (k) { add(s, k, item.stats[k] * mult); });
      var p = item.passive || {};
      if (p.critdmg) s.critdmg += p.critdmg;
      if (p.mpenpct) s.mpenpct += p.mpenpct;
      if (p.ap_pct) pct.ap += p.ap_pct;
      if (p.gold_pct) pct.gold += p.gold_pct;
      ["execute", "extra_hits", "extra_ratio", "current_hp_pct", "auto_pct", "onhit_ap", "burn_maxhp_pct", "thorns",
        "regen_maxhp_pct", "shred", "epic_pct"].forEach(function (k) { if (p[k]) flags[k] = Math.max(flags[k] || 0, p[k]); });
      ["kraken", "sunfire", "sterak"].forEach(function (k) { if (p[k]) flags[k] = true; });
      if (p.stack === "heartsteel") s.hp += 30 * (stacks.heartsteel || 0) * stackMult * heartMult;
      else if (p.stack === "tear") {
        var charges = (stacks.tear || 0) * stackMult;
        if (slot.id === "3042") s.ad += 7.2 + 0.4 * Math.sqrt(Math.max(0, charges - 360));
        else { s.hp += Math.min(charges, 360); s.ad += 0.02 * Math.min(charges, 360); }
      } else if (p.stack === "mejai") {
        var m = (stacks.mejai || 0) * stackMult;
        s.ap += p.max ? 2 * Math.min(m, p.max) : 4 * Math.sqrt(m);
      }
    });
    (run.elixirs || []).forEach(function (x) {
      var item = catalog[x.id];
      if (!item || x.until <= now) return;
      Object.keys(item.stats).forEach(function (k) { add(s, k, item.stats[k]); });
    });
    (run.drakes || []).forEach(function (kind) {
      var b = drakeBuffs[kind] || {};
      var mult = run.soul === kind ? 2 : 1;
      pct.ad += (b.ad_pct || 0) * mult;
      pct.ap += (b.ap_pct || 0) * mult;
      pct.armor += (b.armor_pct || 0) * mult;
      pct.mr += (b.armor_pct || 0) * mult;
      s.regen_flat += (b.regen_flat || 0) * mult;
      s.mspct += (b.ms_pct || 0) * mult;
      s.as += (b.as || 0) * mult;
      if (b.low_hp_dmg) flags.low_hp_dmg = (flags.low_hp_dmg || 0) + b.low_hp_dmg * mult;
    });
    if (run.grubs) pct.dmg += 2 * run.grubs;
    var hpMult = 1;
    active.forEach(function (code) {
      var a = augments[code] || {};
      pct.ad += a.ad_pct || 0; pct.ap += a.ap_pct || 0; pct.hp += a.hp_pct || 0;
      pct.dmg += a.dmg_pct || 0; pct.gold += a.gold_pct || 0;
      s.crit += a.crit || 0; s.as += a.as || 0; s.omnivamp += a.omnivamp || 0; s.mspct += a.ms_pct || 0;
      if (a.hp_mult) hpMult *= a.hp_mult;
      ["epic_pct", "below_half_pct", "double", "minion_hp_pct", "discount"].forEach(function (k) { if (a[k]) flags[k] = (flags[k] || 0) + a[k]; });
      ["magic_crit", "banker"].forEach(function (k) { if (a[k]) flags[k] = true; });
      if (a.as_mult) flags.as_mult = (flags.as_mult || 1) * a.as_mult;
      if (a.stack === "tank_engine") pct.hp += Math.min(50, (stacks.tank_engine || 0) * stackMult);
    });
    // Buffs de monstres (temporaires, seulement côté navigateur).
    (run.buffs || []).forEach(function (b) {
      if (b.until <= now) return;
      var e = enemyBuffs[b.kind] || {};
      pct.ad += e.ad_pct || 0; pct.ap += e.ap_pct || 0; pct.gold += e.gold_pct || 0; pct.magic += e.magic_pct || 0;
      s.crit += e.crit || 0; s.mspct += e.ms_pct || 0;
      if (e.burn_pct) flags.burn_pct = (flags.burn_pct || 0) + e.burn_pct;
      if (e.regen_pct) s.regen += e.regen_pct;
      if (e.execute) flags.execute = Math.max(flags.execute || 0, e.execute);
    });
    s.ad *= 1 + pct.ad / 100;
    s.ap *= 1 + pct.ap / 100;
    s.hp *= (1 + pct.hp / 100) * hpMult;
    s.armor *= 1 + pct.armor / 100;
    s.mr *= 1 + pct.mr / 100;
    s.crit = Math.min(100, s.crit);
    s.ms_total = s.ms * (1 + s.mspct / 100);
    s.autos = s.as / 100 * AUTOS_PER_AS * (flags.as_mult || 1);
    s.dmg_pct = pct.dmg;
    s.gold_pct = pct.gold;
    s.magic_pct = pct.magic;
    s.flags = flags;
    return s;
  }

  function mitigation(resist) { return resist >= 0 ? 100 / (100 + resist) : 2 - 100 / (100 - resist); }

  // Un combat contre un ennemi. rng : fonction () → [0, 1).
  function Fight(enemy, stats, opts) {
    opts = opts || {};
    this.enemy = enemy;
    this.stats = stats;
    this.rng = opts.rng || Math.random;
    var minionCut = (stats.flags.minion_hp_pct || 0);
    var isMinion = ["melee", "caster", "siege", "super"].indexOf(enemy.kind) >= 0;
    this.isMinion = isMinion;
    this.epic = !!opts.epic;
    this.maxHp = Math.max(1, enemy.hp * (isMinion ? 1 + minionCut / 100 : 1));
    this.hp = this.maxHp;
    this.playerMax = stats.hp;
    this.playerHp = stats.hp;
    this.autoTimer = 0;
    this.autoCount = 0;
    this.sterakUsed = false;
    this.done = null;
    this.dealt = 0;
    this.clicks = 0;
    this.t = 0;
    this.enrageAt = this.epic ? ENRAGE_AFTER_EPIC : isMinion ? ENRAGE_AFTER : ENRAGE_AFTER_CAMP;
  }

  Fight.prototype.enraged = function () { return this.t > this.enrageAt; };

  Fight.prototype.damageMult = function () {
    var s = this.stats, f = s.flags, m = 1 + s.dmg_pct / 100;
    if (this.epic && f.epic_pct) m *= 1 + f.epic_pct / 100;
    if (f.below_half_pct && this.hp < this.maxHp / 2) m *= 1 + f.below_half_pct / 100;
    if (f.low_hp_dmg) m *= 1 + f.low_hp_dmg / 100 * (1 - this.playerHp / this.playerMax);
    return m;
  };

  Fight.prototype.hitDamage = function (isAuto, ratio) {
    var s = this.stats, f = s.flags, e = this.enemy;
    var crit = this.rng() < s.crit / 100;
    var critMult = crit ? s.critdmg / 100 : 1;
    var armor = e.armor * (1 - (f.shred || 0) / 100) * (1 - s.armorpen / 100) - s.lethality;
    var mr = e.mr * (1 - s.mpenpct / 100) - s.mpen;
    var physical = s.ad * critMult;
    if (isAuto && f.auto_pct) physical *= 1 + f.auto_pct / 100;
    if (f.current_hp_pct) physical += Math.min(this.hp * f.current_hp_pct / 100, s.ad);
    var magic = 0.3 * s.ap + (isAuto || true ? (f.onhit_ap || 0) * s.ap : 0);
    if (f.magic_crit && crit) magic *= s.critdmg / 100;
    magic *= 1 + (s.magic_pct || 0) / 100;
    var trueDmg = f.burn_pct ? s.ad * f.burn_pct / 100 : 0;
    if (isAuto && f.kraken) { this.autoCount++; if (this.autoCount % 3 === 0) trueDmg += 0.8 * s.ad; }
    var dmg = (physical * mitigation(Math.max(0, armor)) + magic * mitigation(Math.max(0, mr)) + trueDmg) * (ratio || 1);
    return { dmg: dmg * this.damageMult(), physical: physical * mitigation(Math.max(0, armor)) * (ratio || 1), crit: crit };
  };

  Fight.prototype.apply = function (dmg, physical) {
    if (this.done) return 0;
    var s = this.stats;
    this.hp -= dmg;
    this.dealt += dmg;
    var heal = physical * s.lifesteal / 100 + dmg * s.omnivamp / 100;
    if (heal) this.playerHp = Math.min(this.playerMax, this.playerHp + heal);
    var exec = s.flags.execute || 0;
    if (this.hp <= 0 || (exec && this.hp < this.maxHp * exec / 100)) { this.hp = 0; this.done = "win"; }
    return dmg;
  };

  Fight.prototype.strike = function (isAuto) {
    var f = this.stats.flags, total = 0, crit = false;
    var times = 1 + (f.double && this.rng() < f.double / 100 ? 1 : 0);
    for (var i = 0; i < times; i++) {
      var h = this.hitDamage(isAuto, 1);
      crit = crit || h.crit;
      total += this.apply(h.dmg, h.physical);
      if (isAuto && f.extra_hits) {
        for (var j = 0; j < f.extra_hits; j++) {
          var x = this.hitDamage(true, f.extra_ratio || 0.4);
          total += this.apply(x.dmg, x.physical);
        }
      }
    }
    return { dmg: total, crit: crit };
  };

  Fight.prototype.click = function () { this.clicks++; return this.strike(false); };

  Fight.prototype.smite = function () {
    if (this.isMinion || this.done) return 0;
    return this.apply(this.maxHp * SMITE_PCT / 100, 0);
  };

  // Avance de dt secondes : attaques de base, brûlures, dégâts subis, régénération.
  Fight.prototype.tick = function (dt) {
    if (this.done) return this.done;
    var s = this.stats, f = s.flags, e = this.enemy;
    if (s.autos > 0) {
      this.autoTimer += dt * s.autos;
      while (this.autoTimer >= 1 && !this.done) { this.autoTimer -= 1; this.strike(true); }
    }
    var burn = 0;
    if (f.burn_maxhp_pct) burn += this.maxHp * f.burn_maxhp_pct / 100 * (this.epic ? 0.3 : 1);
    if (f.sunfire) burn += 12 + this.playerMax * 0.01;
    if (burn && !this.done) this.apply(burn * dt * mitigation(Math.max(0, e.mr - s.mpen)) * this.damageMult(), 0);
    if (this.done) return this.done;
    this.t += dt;
    var rage = this.t > this.enrageAt ? Math.pow(ENRAGE_RATE, this.t - this.enrageAt) : 1;
    var taken = e.dps * rage * dt * mitigation(e.dmg === "magic" ? s.mr : s.armor);
    this.playerHp -= taken;
    if (f.thorns && taken > 0) this.apply(taken * f.thorns / 100, 0);
    var regen = s.regen_flat * (1 + s.regen / 100) + (f.regen_maxhp_pct ? this.playerMax * f.regen_maxhp_pct / 100 : 0);
    this.playerHp = Math.min(this.playerMax, this.playerHp + regen * dt);
    if (f.sterak && !this.sterakUsed && this.playerHp < this.playerMax * 0.3) {
      this.sterakUsed = true;
      this.playerHp += this.playerMax * 0.25;
    }
    if (this.done) return this.done;
    if (this.playerHp <= 0) { this.playerHp = 0; this.done = "lose"; }
    return this.done;
  };

  function travelTime(stats) { return TRAVEL_SECONDS * BASE_MS / stats.ms_total; }
  function smiteCooldown(stats) { return SMITE_COOLDOWN * BASE_MS / stats.ms_total; }

  var FARM_PENALTY = 0.05, FARM_MIN = 0.1;
  // L'or d'un sbire : moins s'il est loin sous ta meilleure étape.
  function goldFor(enemy, stats, unspent, best) {
    if (!enemy.gold) return 0;
    var bonus = stats.gold_pct;
    if (stats.flags.banker) bonus += Math.min(50, Math.floor(unspent / 1000));
    var gap = Math.max(0, (best || 0) - enemy.step - 1);
    return enemy.gold * (1 + bonus / 100) * Math.max(FARM_MIN, 1 - FARM_PENALTY * gap);
  }

  // Le coût d'achat d'un objet avec les composants déjà dans l'inventaire
  // (comme en jeu) : {cost, consumed: [index d'inventaire]}.
  function purchase(itemId, inv, catalog) {
    var used = {};
    function cost(id) {
      var item = catalog[id];
      var total = item.recipe;
      (item.from || []).forEach(function (sub) {
        for (var i = 0; i < inv.length; i++) {
          if (!used[i] && inv[i].id === sub && !inv[i].ornn) { used[i] = true; return; }
        }
        total += cost(sub);
      });
      return total;
    }
    var c = cost(itemId);
    return { cost: c, consumed: Object.keys(used).map(Number) };
  }

  return {
    computeStats: computeStats, Fight: Fight, travelTime: travelTime, smiteCooldown: smiteCooldown,
    goldFor: goldFor, purchase: purchase, MAX_CPS: MAX_CPS, ORNN_MULT: ORNN_MULT,
    ENRAGE_AFTER: ENRAGE_AFTER, ENRAGE_AFTER_EPIC: ENRAGE_AFTER_EPIC,
  };
});

// La Faille : le moteur de combat, sans interface. Utilisé par la page
// (static/js/faille.js) et par le simulateur de calibrage (Node). Les stats
// d'un build sont calculées comme dans cards/faille_rules.py (compute_stats).
//
// Trois sources de dégâts : les clics (dégâts par clic, coups critiques, vol
// de vie : la branche physique), les dégâts passifs (puissance × AP_DPS par
// seconde, sans cliquer : la branche magique) et les brûlures/renvois des
// tanks. Les PV et la régénération font durer les combats.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.FailleEngine = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var BASE = { ad: 20, ap: 0, crit: 0, critdmg: 150, hp: 450, regen: 4, lifesteal: 0, omnivamp: 0, goldgen: 1 };
  var AP_DPS = 2.5;
  var ORNN_MULT = 1.5;
  var MAX_CPS = 15;
  var TRAVEL_SECONDS = 0.6;
  // Au-delà de ce temps, l'ennemi s'enrage : ses dégâts augmentent de 12 % par seconde.
  var ENRAGE_AFTER = 20;
  var ENRAGE_AFTER_EPIC = 60;
  var ENRAGE_AFTER_CAMP = 40;
  var ENRAGE_RATE = 1.12;
  var ZHONYA_SECONDS = 3;

  function add(obj, key, v) { obj[key] = (obj[key] || 0) + v; }

  // run : {inv: [{id, ornn}], stacks, drakes, soul, grubs, augments, temp: [{code, until}], elixirs, buffs: [{kind, until}]}
  function computeStats(run, catalog, augments, drakeBuffs, enemyBuffs, now) {
    var s = {};
    Object.keys(BASE).forEach(function (k) { s[k] = BASE[k]; });
    var pct = { ad: 0, ap: 0, hp: 0, dmg: 0, gold: 0, passive: 0 };
    var flags = {};
    var stacks = run.stacks || {};
    var active = (run.augments || []).slice();
    (run.temp || []).forEach(function (t) { if (t.until > now) active.push(t.code); });
    var stackMult = 1, heartMult = 1, passiveMult = 1, regenMult = 1;
    active.forEach(function (code) {
      var a = augments[code] || {};
      if (a.stack_mult) stackMult *= a.stack_mult;
      if (a.heartsteel_mult) heartMult *= a.heartsteel_mult;
    });
    (run.inv || []).forEach(function (slot) {
      var item = catalog[slot.id];
      if (!item) return;
      var mult = slot.ornn ? ORNN_MULT : 1;
      Object.keys(item.stats).forEach(function (k) {
        if (k === "gold") pct.gold += item.stats[k] * mult;
        else add(s, k, item.stats[k] * mult);
      });
      var p = item.passive || {};
      if (p.critdmg) s.critdmg += p.critdmg;
      if (p.ap_pct) pct.ap += p.ap_pct;
      if (p.gold_pct) pct.gold += p.gold_pct;
      if (p.passive_pct) pct.passive += p.passive_pct;
      ["execute", "current_hp_pct", "onhit_ap", "burn_maxhp_pct", "thorns", "regen_maxhp_pct", "epic_pct", "heal_pct"]
        .forEach(function (k) { if (p[k]) flags[k] = Math.max(flags[k] || 0, p[k]); });
      ["kraken", "sunfire", "sterak", "zhonya"].forEach(function (k) { if (p[k]) flags[k] = true; });
      if (p.stack === "heartsteel") s.hp += 30 * (stacks.heartsteel || 0) * stackMult * heartMult;
      else if (p.stack === "tear") {
        var charges = (stacks.tear || 0) * stackMult;
        if (slot.id === "3042") s.ad += 7.2 + 0.4 * Math.sqrt(Math.max(0, charges - 360));
        else s.ad += 0.02 * Math.min(charges, 360);
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
      ["ad", "ap", "hp", "passive"].forEach(function (k) { pct[k] += (b[k + "_pct"] || 0) * mult; });
      s.regen += (b.regen_flat || 0) * mult;
      s.crit += (b.crit || 0) * mult;
      if (b.low_hp_dmg) flags.low_hp_dmg = (flags.low_hp_dmg || 0) + b.low_hp_dmg * mult;
    });
    if (run.grubs) pct.dmg += 2 * run.grubs;
    var hpMult = 1;
    active.forEach(function (code) {
      var a = augments[code] || {};
      ["ad", "ap", "hp", "dmg", "gold", "passive"].forEach(function (k) { pct[k] += a[k + "_pct"] || 0; });
      s.crit += a.crit || 0; s.critdmg += a.critdmg || 0; s.omnivamp += a.omnivamp || 0;
      if (a.hp_mult) hpMult *= a.hp_mult;
      if (a.passive_mult) passiveMult *= a.passive_mult;
      ["epic_pct", "below_half_pct", "double", "minion_hp_pct", "discount"].forEach(function (k) { if (a[k]) flags[k] = (flags[k] || 0) + a[k]; });
      if (a.banker) flags.banker = true;
      if (a.stack === "tank_engine") pct.hp += Math.min(50, (stacks.tank_engine || 0) * stackMult);
    });
    // Buffs de monstres (temporaires, seulement côté navigateur).
    (run.buffs || []).forEach(function (b) {
      if (b.until <= now) return;
      var e = enemyBuffs[b.kind] || {};
      ["ad", "ap", "gold", "passive"].forEach(function (k) { pct[k] += e[k + "_pct"] || 0; });
      s.crit += e.crit || 0;
      if (e.burn_pct) flags.burn_pct = (flags.burn_pct || 0) + e.burn_pct;
      if (e.regen_pct) regenMult += e.regen_pct / 100;
      if (e.execute) flags.execute = Math.max(flags.execute || 0, e.execute);
    });
    s.ad *= 1 + pct.ad / 100;
    s.ap *= 1 + pct.ap / 100;
    s.hp *= (1 + pct.hp / 100) * hpMult;
    s.crit = Math.min(100, s.crit);
    s.regen *= regenMult;
    s.passive_pct = pct.passive;
    s.passive_mult = passiveMult;
    s.passive_dps = s.ap * AP_DPS * (1 + pct.passive / 100) * passiveMult;
    s.dmg_pct = pct.dmg;
    s.gold_pct = pct.gold;
    s.flags = flags;
    // Ce qu'un clic moyen inflige (affichage).
    s.click_avg = s.ad * (1 + s.crit / 100 * (s.critdmg / 100 - 1)) + (flags.onhit_ap || 0) * s.ap;
    return s;
  }

  // Un combat contre un ennemi. rng : fonction () → [0, 1).
  function Fight(enemy, stats, opts) {
    opts = opts || {};
    this.enemy = enemy;
    this.stats = stats;
    this.rng = opts.rng || Math.random;
    var isMinion = ["melee", "caster", "siege", "super"].indexOf(enemy.kind) >= 0;
    this.isMinion = isMinion;
    this.epic = !!opts.epic;
    this.maxHp = Math.max(1, enemy.hp * (isMinion ? 1 + (stats.flags.minion_hp_pct || 0) / 100 : 1));
    this.hp = this.maxHp;
    this.playerMax = stats.hp;
    this.playerHp = stats.hp;
    this.clickCount = 0;
    this.sterakUsed = false;
    this.zhonyaUntil = -1;
    this.done = null;
    this.dealt = 0;
    this.clicks = 0;
    this.t = 0;
    this.enrageAt = this.epic ? ENRAGE_AFTER_EPIC : isMinion ? ENRAGE_AFTER : ENRAGE_AFTER_CAMP;
  }

  Fight.prototype.enraged = function () { return this.t > this.enrageAt; };
  Fight.prototype.inStasis = function () { return this.t < this.zhonyaUntil; };

  Fight.prototype.damageMult = function () {
    var s = this.stats, f = s.flags, m = 1 + s.dmg_pct / 100;
    if (this.epic && f.epic_pct) m *= 1 + f.epic_pct / 100;
    if (f.below_half_pct && this.hp < this.maxHp / 2) m *= 1 + f.below_half_pct / 100;
    if (f.low_hp_dmg) m *= 1 + f.low_hp_dmg / 100 * (1 - this.playerHp / this.playerMax);
    return m;
  };

  Fight.prototype.heal = function (amount) {
    var f = this.stats.flags;
    this.playerHp = Math.min(this.playerMax, this.playerHp + amount * (1 + (f.heal_pct || 0) / 100));
  };

  Fight.prototype.apply = function (dmg) {
    if (this.done) return 0;
    this.hp -= dmg;
    this.dealt += dmg;
    var exec = this.stats.flags.execute || 0;
    if (this.hp <= 0 || (exec && this.hp < this.maxHp * exec / 100)) { this.hp = 0; this.done = "win"; }
    return dmg;
  };

  // Un coup de clic : dégâts par clic (critiques), effets des objets physiques.
  Fight.prototype.hit = function () {
    var s = this.stats, f = s.flags;
    var crit = this.rng() < s.crit / 100;
    var dmg = s.ad * (crit ? s.critdmg / 100 : 1);
    if (f.current_hp_pct) dmg += Math.min(this.hp * f.current_hp_pct / 100, 2 * s.ad);
    if (f.burn_pct) dmg += s.ad * f.burn_pct / 100;
    this.clickCount++;
    if (f.kraken && this.clickCount % 3 === 0) dmg += 0.8 * s.ad;
    dmg += (f.onhit_ap || 0) * s.ap;
    dmg *= this.damageMult();
    this.apply(dmg);
    this.heal(dmg * (s.lifesteal + s.omnivamp) / 100);
    return { dmg: dmg, crit: crit };
  };

  Fight.prototype.click = function () {
    if (this.done) return { dmg: 0, crit: false };
    this.clicks++;
    var f = this.stats.flags;
    var r = this.hit();
    if (f.double && !this.done && this.rng() < f.double / 100) {
      var r2 = this.hit();
      r = { dmg: r.dmg + r2.dmg, crit: r.crit || r2.crit };
    }
    return r;
  };

  // Les dégâts passifs d'une seconde (affichage).
  Fight.prototype.passivePerSecond = function () {
    var s = this.stats, f = s.flags;
    var dps = s.passive_dps;
    if (f.burn_maxhp_pct) dps += Math.min(this.maxHp * f.burn_maxhp_pct / 100 * (this.epic ? 0.3 : 1), 2 * s.ap) * (1 + s.passive_pct / 100);
    if (f.sunfire) dps += 12 + this.playerMax * 0.01;
    return dps * this.damageMult();
  };

  // Avance de dt secondes : dégâts passifs, dégâts subis, régénération.
  Fight.prototype.tick = function (dt) {
    if (this.done) return this.done;
    var s = this.stats, f = s.flags, e = this.enemy;
    var passive = this.passivePerSecond() * dt;
    if (passive) { this.apply(passive); this.heal(passive * s.omnivamp / 100); }
    if (this.done) return this.done;
    this.t += dt;
    var rage = this.t > this.enrageAt ? Math.pow(ENRAGE_RATE, this.t - this.enrageAt) : 1;
    var taken = this.inStasis() ? 0 : e.dps * rage * dt;
    this.playerHp -= taken;
    if (f.thorns && taken > 0) this.apply(taken * f.thorns / 100 * this.damageMult());
    var regen = s.regen + (f.regen_maxhp_pct ? this.playerMax * f.regen_maxhp_pct / 100 : 0);
    this.heal(regen * dt);
    if (f.sterak && !this.sterakUsed && this.playerHp < this.playerMax * 0.3) {
      this.sterakUsed = true;
      this.playerHp += this.playerMax * 0.25;
    }
    if (f.zhonya && this.zhonyaUntil < 0 && this.playerHp < this.playerMax * 0.3) this.zhonyaUntil = this.t + ZHONYA_SECONDS;
    if (this.done) return this.done;
    if (this.playerHp <= 0) { this.playerHp = 0; this.done = "lose"; }
    return this.done;
  };

  function travelTime() { return TRAVEL_SECONDS; }

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
    computeStats: computeStats, Fight: Fight, travelTime: travelTime,
    goldFor: goldFor, purchase: purchase, MAX_CPS: MAX_CPS, ORNN_MULT: ORNN_MULT, AP_DPS: AP_DPS,
    ENRAGE_AFTER: ENRAGE_AFTER, ENRAGE_AFTER_EPIC: ENRAGE_AFTER_EPIC,
  };
});

// La Faille : l'interface. Le combat se joue ici (faille-engine.js) ; le
// serveur reçoit une sauvegarde toutes les 30 s et gère l'or, la boutique et
// les augments (cards/faille.py).
(function () {
  "use strict";
  var E = window.FailleEngine;
  var root = document.getElementById("faille");
  var data = JSON.parse(document.getElementById("faille-data").textContent);
  var csrf = root.getAttribute("data-csrf");
  var cat = data.catalog;
  var AUG = data.augments;
  var state = data.state;
  var clockOffset = data.now - Date.now() / 1000; // heure du serveur
  function now() { return Date.now() / 1000 + clockOffset; }

  var enemies = {};
  data.enemies.forEach(function (e) { enemies[e.step] = e; });

  // ------------------------------------------------------------------ Utilitaires
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "text") node.textContent = v;
      else if (k.indexOf("on") === 0) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return node;
  }
  function fmt(x, digits) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    var n = Math.abs(x) >= 100 ? Math.round(x) : Math.round(x * Math.pow(10, digits || 0)) / Math.pow(10, digits || 0);
    return n.toLocaleString("fr-FR");
  }
  function big(x) {
    if (x >= 1e6) return (x / 1e6).toLocaleString("fr-FR", { maximumFractionDigits: 1 }) + " M";
    if (x >= 1e4) return Math.round(x / 1e3).toLocaleString("fr-FR") + " k";
    return Math.round(x).toLocaleString("fr-FR");
  }
  function post(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(body || {}),
    }).then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }
  function log(text, kind) {
    var list = $("fl-log");
    list.insertBefore(el("li", { class: kind ? "is-" + kind : "", text: text }), list.firstChild);
    while (list.children.length > 6) list.removeChild(list.lastChild);
  }

  $("fl-ends").setAttribute("datetime", new Date(data.ends * 1000).toISOString());
  if (data.offlineGold) {
    var off = $("fl-offline");
    off.hidden = false;
    off.textContent = "Pendant ton absence, ta génération d'or t'a rapporté " + fmt(data.offlineGold) + " PO.";
  }

  // ------------------------------------------------------------------ État local
  var buffs = []; // buffs de monstres : {kind, until}
  var pending = { kills: {}, losses: {}, clicks: 0, gold: 0, duration: 0, noclick: [] };
  var streak = 0, bestStreak = 0;
  var fight = null, travel = 0, smiteReady = 0;
  var autoAdvance = true;
  var stats = null;

  function runState() {
    return { inv: state.inv, stacks: state.stacks, drakes: state.drakes, soul: state.soul, grubs: state.grubs,
      augments: state.augments, temp: state.temp, elixirs: state.elixirs, buffs: buffs };
  }
  function computeStats() {
    stats = E.computeStats(runState(), cat, AUG, data.drakeBuffs, data.enemyBuffs, now());
    return stats;
  }
  function enemyAt(step) { return enemies[step]; }
  function needEnemies() {
    var max = Math.max.apply(null, Object.keys(enemies).map(Number));
    if (state.best + 20 > max && !needEnemies.loading) {
      needEnemies.loading = true;
      fetch(data.urls.enemies + "?from=" + (max + 1), { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (j) { j.enemies.forEach(function (e) { enemies[e.step] = e; }); })
        .finally(function () { needEnemies.loading = false; });
    }
  }
  function isEpic(kind) { return !!(data.enemyInfo[kind] && data.enemyInfo[kind].epic); }

  // ------------------------------------------------------------------ Combat
  function startFight() {
    computeStats();
    var e = enemyAt(state.step);
    if (!e) { needEnemies(); return; }
    fight = new E.Fight(e, stats, { epic: isEpic(e.kind) });
    var bar = $("fl-enemy-hp"), target = $("fl-target");
    bar.style.transition = "none"; bar.style.width = "100%"; void bar.offsetWidth; bar.style.transition = "";
    target.classList.remove("is-dead", "is-away");
    target.removeAttribute("aria-disabled");
    var info = data.enemyInfo[e.kind] || {};
    $("fl-enemy-name").textContent = (e.errant ? "Nashor errant" : info.name || e.kind);
    $("fl-enemy-tag").textContent = (info.epic ? "épique" : info.camp ? "camp" : "sbire") + " · " +
      (e.dmg === "magic" ? "dégâts magiques" : "dégâts physiques") + (e.gold ? " · " + fmt(E.goldFor(e, stats, state.gold, state.best), 1) + " PO" : "");
    var img = $("fl-enemy-img"), ph = $("fl-enemy-ph");
    if (info.image) { if (img.getAttribute("src") !== info.image) img.src = info.image; img.hidden = false; ph.hidden = true; }
    else { img.hidden = true; ph.hidden = false; ph.textContent = (info.name || "?").split(" ").map(function (w) { return w[0]; }).join("").slice(0, 2); }
    $("fl-enemy").className = "fl-enemy" + (info.epic ? " is-epic" : info.camp ? " is-camp" : "");
    $("fl-step").textContent = state.step;
    renderBars();
  }

  function win() {
    var e = fight.enemy;
    pending.kills[e.step] = (pending.kills[e.step] || 0) + 1;
    var g = E.goldFor(e, stats, state.gold, state.best);
    pending.gold += g;
    state.gold += g;
    state.kills = (state.kills || 0) + 1;
    if (e.kind === "baron" && fight.clicks === 0) pending.noclick.push(e.step);
    var buff = data.enemyBuffs[e.kind];
    if (buff) {
      buffs = buffs.filter(function (b) { return b.kind !== e.kind; });
      buffs.push({ kind: e.kind, until: now() + buff.seconds });
      log(buff.label, "buff");
    }
    if (g) float("+" + fmt(g, 1) + " PO", "gold");
    if (e.step === state.best) { state.best += 1; streak += 1; bestStreak = Math.max(bestStreak, streak); }
    if (isEpic(e.kind) && (e.kind === "baron" || e.kind === "elder")) {
      log((e.errant ? "Nashor errant" : data.enemyInfo[e.kind].name) + " vaincu : un augment t'attend.", "epic");
      sync();
    }
    if (autoAdvance && state.step < state.best) state.step += 1;
    endFight("is-dead", "Vaincu · en route vers l'étape " + state.step + "…");
    travel = E.travelTime(stats);
    needEnemies();
  }
  // Fin d'un combat : la barre se vide tout de suite et l'ennemi s'efface
  // pendant le trajet (les clics n'y font plus rien).
  function endFight(cls, text) {
    if (fight) {
      $("fl-enemy-hp").style.width = cls === "is-dead" ? "0%" : $("fl-enemy-hp").style.width;
      $("fl-enemy-hp-text").textContent = text;
    }
    fight = null;
    $("fl-target").classList.add(cls);
    $("fl-target").setAttribute("aria-disabled", "true");
  }
  function lose() {
    var e = fight.enemy;
    pending.losses[e.step] = (pending.losses[e.step] || 0) + 1;
    streak = 0;
    if (state.stacks && state.stacks.mejai) state.stacks.mejai = Math.floor(state.stacks.mejai / 2);
    log((data.enemyInfo[e.kind] || {}).name + " t'a vaincu : retour à l'étape " + Math.max(0, state.step - 1) + ".", "lose");
    state.step = Math.max(0, state.step - 1);
    endFight("is-away", "Repli vers l'étape " + state.step + "…");
    travel = E.travelTime(stats) * 2;
  }

  var lastClicks = [];
  function click(ev) {
    if (!fight || fight.done || document.hidden) return;
    var t = performance.now();
    lastClicks = lastClicks.filter(function (x) { return t - x < 1000; });
    if (lastClicks.length >= E.MAX_CPS) return;
    lastClicks.push(t);
    pending.clicks += 1;
    if (state.stacks && ownsAny(["3070", "3004", "3042"])) state.stacks.tear = (state.stacks.tear || 0) + 1;
    var r = fight.click();
    float(big(r.dmg), r.crit ? "crit" : "", ev);
    var hit = $("fl-target");
    hit.classList.remove("is-hit"); void hit.offsetWidth; hit.classList.add("is-hit");
    if (fight.done === "win") win();
    else renderBars();
  }
  function ownsAny(ids) { return (state.inv || []).some(function (s) { return ids.indexOf(s.id) >= 0; }); }

  function smite() {
    if (!fight || fight.done || now() < smiteReady || fight.isMinion) return;
    var dmg = fight.smite();
    smiteReady = now() + E.smiteCooldown(stats);
    float(big(dmg), "smite");
    if (fight.done === "win") win();
  }

  function float(text, kind, ev) {
    var box = $("fl-floats");
    var x = 50 + (Math.random() * 40 - 20), y = 40 + (Math.random() * 20 - 10);
    var node = el("span", { class: "fl-float" + (kind ? " is-" + kind : ""), text: text, style: "left:" + x + "%;top:" + y + "%" });
    box.appendChild(node);
    setTimeout(function () { node.remove(); }, 900);
  }

  // ------------------------------------------------------------------ Boucle
  var last = performance.now();
  var renderTick = 0;
  function loop() {
    var t = performance.now();
    var dt = Math.min(0.25, (t - last) / 1000);
    last = t;
    if (!document.hidden) {
      pending.duration += dt;
      buffs = buffs.filter(function (b) { return b.until > now(); });
      if (!fight) {
        travel -= dt;
        if (travel <= 0) startFight();
      } else {
        fight.stats = computeStats();
        var res = fight.tick(dt);
        if (res === "win") win();
        else if (res === "lose") lose();
      }
      renderTick += dt;
      if (renderTick > 0.2) { renderTick = 0; renderBars(); renderCounters(); renderBuffs(); }
    }
    requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------------ Sauvegarde
  var syncing = null;
  function sync() {
    if (syncing) return syncing;
    var body = {
      duration: pending.duration, clicks: pending.clicks, gold: pending.gold, step: state.step,
      kills: Object.keys(pending.kills).map(function (s) { return [Number(s), pending.kills[s]]; }),
      losses: Object.keys(pending.losses).map(function (s) { return [Number(s), pending.losses[s]]; }),
      streak: bestStreak, noclick: pending.noclick,
    };
    var sent = pending;
    pending = { kills: {}, losses: {}, clicks: 0, gold: 0, duration: 0, noclick: [] };
    bestStreak = streak;
    syncing = post(data.urls.sync, body).then(function (j) {
      if (j.error) log("Sauvegarde refusée : " + j.error, "lose");
      applyState(j.state, j._status === 409);
      (j.events || []).forEach(event);
    }).catch(function () {
      // Hors ligne : on rejoue la sauvegarde à la prochaine occasion.
      Object.keys(sent.kills).forEach(function (s) { pending.kills[s] = (pending.kills[s] || 0) + sent.kills[s]; });
      Object.keys(sent.losses).forEach(function (s) { pending.losses[s] = (pending.losses[s] || 0) + sent.losses[s]; });
      pending.clicks += sent.clicks; pending.gold += sent.gold; pending.duration += sent.duration;
    }).finally(function () { syncing = null; });
    return syncing;
  }
  function applyState(s, reset) {
    if (!s) return;
    var step = state.step;
    state = s;
    if (!reset) state.step = Math.min(step, state.best);
    renderAll();
  }
  function event(ev) {
    if (ev.type === "drake") log("Buff de dragon : " + data.drakeBuffs[ev.kind].label + ".", "epic");
    else if (ev.type === "soul") log("Âme du dragon obtenue : ses buffs comptent double.", "epic");
    else if (ev.type === "transform") log((cat[ev.item] || {}).name + " : ton objet s'est transformé.", "epic");
    else if (ev.type === "achievement") {
      var a = data.achievements.filter(function (x) { return x.code === ev.code; })[0];
      if (a) { a.done = true; log("Succès : " + a.label + " (+" + data.achievementBonus.toLocaleString("fr-FR") + " % à tes équipes).", "epic"); }
      renderAchievements();
    }
  }
  setInterval(sync, 30000);
  document.addEventListener("visibilitychange", function () { if (document.hidden) sync(); });
  window.addEventListener("pagehide", function () {
    if (!pending.duration) return;
    var body = JSON.stringify({
      duration: pending.duration, clicks: pending.clicks, gold: pending.gold, step: state.step, streak: bestStreak,
      kills: Object.keys(pending.kills).map(function (s) { return [Number(s), pending.kills[s]]; }),
      losses: Object.keys(pending.losses).map(function (s) { return [Number(s), pending.losses[s]]; }),
      noclick: pending.noclick,
    });
    fetch(data.urls.sync, { method: "POST", credentials: "same-origin", keepalive: true,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: body });
  });

  // Une action de boutique : on sauvegarde d'abord (l'or des derniers combats).
  function act(url, body, done) {
    return sync().then(function () { return post(url, body); }).then(function (j) {
      if (j.error) { log(j.error, "lose"); }
      else if (done) done(j);
      applyState(j.state);
      return j;
    });
  }

  // ------------------------------------------------------------------ Affichage
  function renderBars() {
    if (!fight) return;
    var p = Math.max(0, fight.hp / fight.maxHp);
    $("fl-enemy-hp").style.width = (p * 100) + "%";
    $("fl-enemy-hp-text").textContent = big(Math.max(0, fight.hp)) + " / " + big(fight.maxHp);
    var q = Math.max(0, fight.playerHp / fight.playerMax);
    $("fl-player-hp").style.width = (q * 100) + "%";
    $("fl-player-hp-text").textContent = big(Math.max(0, fight.playerHp)) + " / " + big(fight.playerMax) + " PV";
    $("fl-rage").hidden = !fight.enraged();
    var left = smiteReady - now();
    var sm = $("fl-smite");
    sm.disabled = left > 0 || fight.isMinion;
    $("fl-smite-cd").textContent = fight.isMinion ? "monstres seulement" : left > 0 ? Math.ceil(left) + " s" : "prêt";
  }
  function renderCounters() {
    $("fl-kills").textContent = big(state.kills || 0);
    $("fl-best").textContent = state.best;
    $("fl-gold").textContent = big(state.gold) + " PO";
    $("fl-step").textContent = state.step;
    $("fl-prev").disabled = state.step <= 0;
    $("fl-next").disabled = state.step >= state.best;
  }
  function renderBuffs() {
    var list = $("fl-buffs");
    list.textContent = "";
    var t = now();
    buffs.forEach(function (b) {
      var info = data.enemyInfo[b.kind] || {};
      list.appendChild(el("li", { title: data.enemyBuffs[b.kind].label }, [
        info.image ? el("img", { src: info.image, alt: "" }) : null,
        el("span", { text: Math.ceil(b.until - t) + " s" }),
      ]));
    });
    (state.temp || []).forEach(function (x) {
      if (x.until <= t) return;
      var a = AUG[x.code];
      list.appendChild(el("li", { class: "is-aug", title: a.name + " : " + a.text }, [
        el("img", { src: a.icon, alt: "" }), el("span", { text: Math.ceil((x.until - t) / 60) + " min" }),
      ]));
    });
    (state.elixirs || []).forEach(function (x) {
      if (x.until <= t) return;
      var it = cat[x.id];
      list.appendChild(el("li", { class: "is-elixir", title: it.name }, [
        el("img", { src: it.icon, alt: "" }), el("span", { text: Math.ceil((x.until - t) / 60) + " min" }),
      ]));
    });
  }

  var STAT_ROWS = [
    ["ad", "Dégâts d'attaque"], ["ap", "Puissance"], ["autos", "Attaques/s", 2], ["crit", "Coup critique", 0, "%"],
    ["critdmg", "Dégâts critiques", 0, "%"], ["hp", "PV"], ["armor", "Armure"], ["mr", "Résistance magique"],
    ["lethality", "Létalité"], ["armorpen", "Pénétration d'armure", 0, "%"], ["mpen", "Pénétration magique"],
    ["lifesteal", "Vol de vie", 0, "%"], ["omnivamp", "Omnivampirisme", 0, "%"], ["ms_total", "Vitesse de déplacement"],
    ["goldgen", "PO toutes les 10 s", 1],
  ];
  function renderStats() {
    var s = computeStats();
    var dl = $("fl-stats");
    dl.textContent = "";
    STAT_ROWS.forEach(function (r) {
      var v = s[r[0]];
      if (!v && ["lethality", "armorpen", "mpen", "lifesteal", "omnivamp", "ap"].indexOf(r[0]) >= 0) return;
      dl.appendChild(el("div", {}, [el("dt", { text: r[1] }), el("dd", { text: fmt(v, r[2]) + (r[3] || "") })]));
    });
  }

  function renderInventory() {
    var list = $("fl-inv");
    list.textContent = "";
    for (var i = 0; i < data.rules.slots; i++) {
      (function (index) {
        var slot = state.inv[index];
        if (!slot) { list.appendChild(el("li", { class: "fl-slot-empty" })); return; }
        var it = cat[slot.id] || { name: slot.id };
        list.appendChild(el("li", {}, [el("button", {
          type: "button", class: "fl-item-btn" + (slot.ornn ? " is-ornn" : ""), title: it.name,
          onclick: function () { openSlot(index); },
        }, [el("img", { src: it.icon, alt: it.name })])]));
      })(i);
    }
  }

  function openSlot(index) {
    var slot = state.inv[index];
    var it = cat[slot.id];
    var dlg = $("fl-slot");
    $("fl-slot-title").textContent = (slot.ornn ? "Chef-d'œuvre d'Ornn : " : "") + it.name;
    var body = $("fl-slot-body");
    body.textContent = "";
    body.appendChild(itemSummary(it, slot.ornn));
    var actions = $("fl-slot-actions");
    actions.textContent = "";
    var refund = Math.round(it.cost * data.rules.sell * (slot.ornn ? 1 + data.rules.ornn : 1));
    actions.appendChild(el("button", { type: "button", class: "btn btn-small btn-danger", text: "Vendre (" + fmt(refund) + " PO)",
      onclick: function () { dlg.close(); act(data.urls.sell, { slot: index }, function () { log(it.name + " vendu.", ""); }); } }));
    if (it.kind === "legendary" && !slot.ornn) {
      var forge = "forge_" + (it.cls || "").toLowerCase();
      var has = state.augments.indexOf(forge) >= 0;
      var cost = Math.round(it.cost * data.rules.ornn);
      actions.appendChild(el("button", { type: "button", class: "btn btn-small btn-gold", disabled: !has || state.gold < cost,
        title: has ? "" : "Il faut l'augment Forge d'Ornn : " + it.cls,
        text: has ? "Forge d'Ornn (" + fmt(cost) + " PO)" : "Forge d'Ornn : " + it.cls + " requise",
        onclick: function () { dlg.close(); act(data.urls.ornn, { slot: index }, function () { log(it.name + " devient un chef-d'œuvre d'Ornn !", "epic"); }); } }));
    }
    actions.appendChild(el("button", { type: "button", class: "btn btn-small btn-ghost", text: "Fermer", onclick: function () { dlg.close(); } }));
    dlg.showModal();
  }

  function itemSummary(it, ornn) {
    var mult = ornn ? E.ORNN_MULT : 1;
    var box = el("div", { class: "fl-sum" });
    var stats = el("ul", { class: "fl-sum-stats" });
    Object.keys(it.stats || {}).forEach(function (k) {
      if (!data.statNames[k]) return;
      stats.appendChild(el("li", { text: "+" + fmt(it.stats[k] * mult, 1) + " " + data.statNames[k] }));
    });
    box.appendChild(stats);
    if (!stats.children.length && !it.passive) box.appendChild(el("p", { class: "muted small", text: "Aucune stat utile dans la Faille : cet objet sert surtout de composant." }));
    if (it.passive) box.appendChild(el("p", { class: "fl-passive", text: it.passive.label }));
    if (it.cls) box.appendChild(el("p", { class: "muted small", text: "Classe " + it.cls + " (Forge d'Ornn : " + it.cls + ")" }));
    return box;
  }

  // ------------------------------------------------------------------ Boutique
  var KINDS = [["component", "Composants"], ["epic", "Épiques"], ["legendary", "Légendaires"], ["boots", "Bottes"], ["elixir", "Élixirs"]];
  var shopKind = "component", selected = null;
  function renderKinds() {
    var box = $("fl-kinds");
    box.textContent = "";
    KINDS.forEach(function (k) {
      box.appendChild(el("button", { type: "button", role: "tab", class: "fl-kind" + (shopKind === k[0] ? " is-active" : ""),
        "aria-selected": shopKind === k[0] ? "true" : "false", text: k[1],
        onclick: function () { shopKind = k[0]; renderKinds(); renderShop(); } }));
    });
  }
  function costOf(id) {
    var it = cat[id];
    if (it.kind === "elixir") return { cost: state.elixir_price, consumed: [] };
    var p = E.purchase(id, state.inv, cat);
    var discount = (computeStats().flags.discount || 0) / 100;
    return { cost: Math.round(p.cost * (1 - Math.min(0.9, discount))), consumed: p.consumed };
  }
  function renderShop() {
    var box = $("fl-items");
    box.textContent = "";
    var q = $("fl-search").value.trim().toLowerCase();
    var list = Object.keys(cat).map(function (id) { return cat[id]; }).filter(function (it) {
      if (!it.buyable && it.kind !== "elixir") return false;
      if (q) return it.name.toLowerCase().indexOf(q) >= 0;
      return it.kind === shopKind;
    }).sort(function (a, b) { return a.cost - b.cost || a.name.localeCompare(b.name); });
    list.forEach(function (it) {
      var c = costOf(it.id);
      var afford = state.gold >= c.cost;
      box.appendChild(el("button", { type: "button", class: "fl-shop-item" + (selected === it.id ? " is-current" : "") + (afford ? " is-affordable" : ""),
        title: it.name, onclick: function () { selected = it.id; renderShop(); renderDetail(); } }, [
        el("img", { src: it.icon, alt: "", loading: "lazy" }),
        el("span", { class: "fl-price", text: fmt(c.cost) }),
      ]));
    });
    if (!list.length) box.appendChild(el("p", { class: "muted", text: "Aucun objet." }));
  }
  function renderDetail() {
    var box = $("fl-detail");
    box.textContent = "";
    if (!selected) { box.appendChild(el("p", { class: "muted", text: "Choisis un objet pour voir ses stats et sa recette." })); return; }
    var it = cat[selected];
    var c = costOf(it.id);
    box.appendChild(el("div", { class: "fl-detail-head" }, [el("img", { src: it.icon, alt: "" }),
      el("div", {}, [el("h3", { text: it.name }), el("p", { class: "muted small", text: it.plain || "" })])]));
    if (it.kind === "elixir") box.appendChild(el("p", { class: "small", text: "Pendant " + data.rules.elixirMinutes + " min. Chaque élixir acheté cette semaine coûte 30 % de plus que le précédent." }));
    box.appendChild(itemSummary(it, false));
    if (it.from && it.from.length) {
      var tree = el("div", { class: "fl-tree" }, [el("span", { class: "muted small", text: "Recette : " })]);
      it.from.forEach(function (id) {
        var sub = cat[id];
        if (!sub) return;
        var owned = state.inv.some(function (s) { return s.id === id; });
        tree.appendChild(el("button", { type: "button", class: "fl-tree-item" + (owned ? " is-owned" : ""), title: sub.name + (owned ? " (possédé)" : ""),
          onclick: function () { selected = id; shopKind = sub.kind; renderKinds(); renderShop(); renderDetail(); } }, [el("img", { src: sub.icon, alt: sub.name })]));
      });
      tree.appendChild(el("span", { class: "muted small", text: "+ " + fmt(it.recipe) + " PO" }));
      box.appendChild(tree);
    }
    if (it.into && it.into.length) {
      var into = el("div", { class: "fl-tree" }, [el("span", { class: "muted small", text: "Sert à : " })]);
      it.into.slice(0, 8).forEach(function (id) {
        var up = cat[id];
        if (!up) return;
        into.appendChild(el("button", { type: "button", class: "fl-tree-item", title: up.name,
          onclick: function () { selected = id; shopKind = up.kind; renderKinds(); renderShop(); renderDetail(); } }, [el("img", { src: up.icon, alt: up.name })]));
      });
      box.appendChild(into);
    }
    var slotsAfter = state.inv.length - c.consumed.length + 1;
    var reason = state.gold < c.cost ? "Il te manque " + fmt(Math.ceil(c.cost - state.gold)) + " PO"
      : (it.kind !== "elixir" && slotsAfter > data.rules.slots) ? "Inventaire plein" : "";
    box.appendChild(el("button", { type: "button", class: "btn btn-gold", disabled: !!reason,
      text: reason || "Acheter (" + fmt(c.cost) + " PO)",
      onclick: function () {
        var url = it.kind === "elixir" ? data.urls.elixir : data.urls.buy;
        act(url, { item: it.id }, function () { log(it.name + " acheté.", "buy"); });
      } }));
  }
  $("fl-search").addEventListener("input", renderShop);

  // ------------------------------------------------------------------ Augments, bonus, succès
  function renderPerks() {
    var box = $("fl-perks");
    box.textContent = "";
    var row = el("div", { class: "fl-perk-row" });
    (state.drakes || []).forEach(function (k) {
      var info = data.enemyInfo[k];
      row.appendChild(el("span", { class: "fl-perk" + (state.soul === k ? " is-soul" : ""), title: info.name + " : " + data.drakeBuffs[k].label + (state.soul === k ? " (âme : compte double)" : "") },
        [info.image ? el("img", { src: info.image, alt: info.name }) : el("b", { text: info.name[0] })]));
    });
    (state.augments || []).forEach(function (code) {
      var a = AUG[code];
      row.appendChild(el("span", { class: "fl-perk is-" + a.tier, title: a.name + " : " + a.text }, [el("img", { src: a.icon, alt: a.name })]));
    });
    if (!row.children.length) box.appendChild(el("p", { class: "muted small", text: "Les drakes donnent un buff pour la semaine ; le Nashor, un augment au choix." }));
    else box.appendChild(row);
    if (state.grubs) box.appendChild(el("p", { class: "small", text: "Larves du Néant : +" + 2 * state.grubs + " % de dégâts" }));
  }
  function renderAugmentChoice() {
    var dlg = $("fl-augments");
    var offers = state.pending || [];
    if (!offers.length) { if (dlg.open) dlg.close(); return; }
    var box = $("fl-aug-cards");
    box.textContent = "";
    $("fl-aug-sub").textContent = offers.length > 1 ? "Encore " + (offers.length - 1) + " choix après celui-ci." : "Les augments définitifs durent jusqu'à lundi.";
    offers[0].forEach(function (code) {
      var a = AUG[code];
      box.appendChild(el("button", { type: "button", class: "fl-aug is-" + a.tier, onclick: function () {
        act(data.urls.augment, { code: code }, function () { log("Augment : " + a.name + ".", "epic"); });
      } }, [
        el("img", { src: a.icon, alt: "" }),
        el("b", { text: a.name }),
        el("small", { text: { silver: "Argent", gold: "Or", prismatic: "Prismatique" }[a.tier] + (a.kind === "temp" ? " · " + a.minutes + " min" : " · jusqu'à lundi") }),
        el("p", { text: a.text }),
      ]));
    });
    if (!dlg.open) dlg.showModal();
  }
  $("fl-augments").addEventListener("cancel", function (e) { e.preventDefault(); });

  function renderAchievements() {
    var list = $("fl-ach");
    list.textContent = "";
    var done = 0;
    data.achievements.forEach(function (a) {
      if (a.done) done++;
      list.appendChild(el("li", { class: a.done ? "is-done" : "" }, [el("small", { text: a.family }), el("span", { text: a.label })]));
    });
    $("fl-ach-sum").textContent = done + " / " + data.achievements.length + " : +" +
      (Math.round(done * data.achievementBonus * 100) / 100).toLocaleString("fr-FR") + " % au score de tes équipes (permanentes et de la semaine, pas en tournoi).";
  }

  function renderAll() {
    renderCounters(); renderStats(); renderInventory(); renderShop(); renderDetail(); renderPerks(); renderAugmentChoice(); renderBuffs();
  }

  // ------------------------------------------------------------------ Commandes
  $("fl-target").addEventListener("pointerdown", function (e) { e.preventDefault(); click(e); });
  $("fl-target").addEventListener("keydown", function (e) { if (e.key === " " || e.key === "Enter") { e.preventDefault(); click(e); } });
  $("fl-smite").addEventListener("click", smite);
  $("fl-auto").addEventListener("change", function (e) { autoAdvance = e.target.checked; });
  $("fl-prev").addEventListener("click", function () { if (state.step > 0) { state.step -= 1; endFight("is-away", "En route vers l'étape " + state.step + "…"); travel = 0.2; } });
  $("fl-next").addEventListener("click", function () { if (state.step < state.best) { state.step += 1; endFight("is-away", "En route vers l'étape " + state.step + "…"); travel = 0.2; } });

  renderKinds();
  renderAll();
  renderAchievements();
  startFight();
  requestAnimationFrame(loop);
})();

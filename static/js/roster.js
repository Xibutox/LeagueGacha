// Le constructeur d'équipe : un joueur par poste, 5 champions par joueur.
// Les notes affichées dans les sélecteurs viennent des données de la page ;
// les scores (postes, compositions, cohérence, total) viennent du serveur
// (/equipes/apercu/), recalculés à chaque changement.
(function () {
  "use strict";

  var data = JSON.parse(document.getElementById("builder-data").textContent);
  var form = document.getElementById("builder");
  var lanesEl = document.getElementById("lanes");
  var compsEl = document.getElementById("comps");
  var totalEl = document.getElementById("total");
  var totalLabel = document.getElementById("total-label");
  var compScoresEl = document.getElementById("comp-scores");
  var errorsEl = document.getElementById("errors");
  var picker = document.getElementById("picker");
  var pickerTitle = document.getElementById("picker-title");
  var pickerGrid = document.getElementById("picker-grid");
  var pickerSearch = document.getElementById("picker-search");
  var pickerPlayed = document.getElementById("picker-played");
  var pickerPlayedWrap = document.getElementById("picker-played-wrap");
  var pickerNote = document.getElementById("picker-note");
  var csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;
  var N = data.compositions;

  var champions = {};
  data.champions.forEach(function (c) { champions[c.id] = c; });
  var cards = {};
  data.roles.forEach(function (role) {
    (data.cards[role] || []).forEach(function (c) { cards[c.id] = c; });
  });

  // État : {rôle: {card: id|null, champions: [id|null × 5]}}
  var state = {};
  data.roles.forEach(function (role) {
    var p = data.picks[role] || {};
    var list = (p.champions || []).slice(0, N);
    while (list.length < N) list.push(null);
    state[role] = { card: p.card && cards[p.card] ? p.card : null, champions: list };
  });
  document.getElementById("team-name").value = data.name || "";

  var result = null; // la dernière réponse du serveur

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (attrs[k] === null || attrs[k] === undefined) return;
      if (k === "text") node.textContent = attrs[k];
      else if (k === "style") node.setAttribute("style", attrs[k]);
      else if (k.indexOf("on") === 0) node.addEventListener(k.slice(2), attrs[k]);
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return node;
  }

  var rules = data.rules || {};
  var metaChampions = data.metaChampions || {};
  // Le bonus de la méta de la semaine pour une carte / un champion à un poste.
  function cardMeta(card) { return card && card.meta ? card.meta : 0; }
  function champMeta(role, championId) { return (metaChampions[role] || {})[championId] || 0; }
  function bonusTag(pct, title) {
    return pct ? el("span", { class: "meta-tag", title: title || "Bonus de la méta de la semaine", text: "+" + pct + " %" }) : null;
  }

  function fmt(x) { return x === null || x === undefined ? "—" : (Math.round(x * 10) / 10).toLocaleString("fr-FR"); }

  // La note d'un joueur sur un champion : son affinité, ou sa note moins la pénalité.
  function rating(cardId, championId) {
    var card = cards[cardId];
    var a = (data.affinities[cardId] || {})[championId];
    if (a) return { rating: a.rating, games: a.games, wins: a.wins, before: a.before, played: true };
    return { rating: Math.max(0, card.rating - data.penalty), games: 0, wins: 0, before: 0, played: false };
  }

  function slotResult(role, index) {
    var s = result && result.slots[role];
    return s ? s.champions[index] : null;
  }

  // ------------------------------------------------------------------ Couloirs
  function renderLanes() {
    lanesEl.textContent = "";
    data.roles.forEach(function (role) {
      var st = state[role];
      var card = st.card ? cards[st.card] : null;
      var lane = el("section", { class: "lane", "aria-label": role });
      var slotScores = result && result.slots[role] ? result.slots[role].champions.filter(Boolean).map(function (e) { return e.score; }) : [];
      var avg = slotScores.length ? slotScores.reduce(function (a, b) { return a + b; }, 0) / slotScores.length : null;
      lane.appendChild(el("header", { class: "lane-head" }, [
        el("h3", { text: role }),
        avg !== null ? el("b", { class: "lane-score", title: "Moyenne du poste", text: fmt(avg) }) : null,
      ]));

      var playerBtn = el("button", {
        type: "button", class: "player-pick" + (card ? " is-set" : ""),
        style: card ? "--rarity:" + card.rarity.color : "",
        onclick: function () { openPlayers(role); },
      });
      if (card) {
        playerBtn.appendChild(el("span", { class: "pp-photo" }, [
          card.photo ? el("img", { src: card.photo, alt: "" }) : el("span", { text: card.ign.slice(0, 2) }),
        ]));
        playerBtn.appendChild(el("span", { class: "pp-text" }, [
          el("b", { text: card.ign + (card.fan ? " ♥" : "") }),
          el("small", { text: card.team + " · " + card.split }),
          el("span", { class: "pp-line" }, [
            el("span", { class: "pp-rating", title: "Note sur la période", text: fmt(card.rating) }),
            el("span", { class: "pp-rarity", text: card.rarity.name + (card.rarity.bonus ? " +" + fmt(card.rarity.bonus) + " %" : "") }),
            bonusTag(cardMeta(card), rules[role] ? rules[role].label : ""),
          ]),
        ]));
      } else {
        playerBtn.appendChild(el("span", { class: "pp-empty", text: (data.cards[role] || []).length ? "Choisir un joueur" : "Aucun " + role + " dans ta collection" }));
        if (!(data.cards[role] || []).length) playerBtn.disabled = true;
      }
      lane.appendChild(playerBtn);

      var list = el("ol", { class: "champ-slots" });
      for (var i = 0; i < N; i++) {
        (function (index) {
          var cid = st.champions[index];
          var champ = cid ? champions[cid] : null;
          var res = slotResult(role, index);
          var li = el("li");
          var btn = el("button", {
            type: "button", class: "champ-slot" + (champ ? " is-set" : ""),
            "aria-label": "Composition " + (index + 1) + " : " + (champ ? champ.name : "choisir un champion"),
            onclick: function () { openChampions(role, index); },
          });
          if (!card) btn.disabled = true;
          btn.appendChild(el("span", { class: "cs-index", text: String(index + 1) }));
          if (champ) {
            var r = rating(st.card, cid);
            btn.appendChild(el("img", { src: champ.icon, alt: "", loading: "lazy" }));
            var cm = champMeta(role, cid);
            btn.appendChild(el("span", { class: "cs-text" }, [
              el("b", { text: champ.name }),
              el("small", { text: (r.played ? "note " + fmt(r.rating) : "jamais joué · " + fmt(r.rating)) + (cm ? " · méta +" + cm + " %" : "") }),
            ]));
            btn.appendChild(el("span", { class: "cs-score", title: "Score du poste dans cette composition", text: res ? fmt(res.score) : "" }));
          } else {
            btn.appendChild(el("span", { class: "cs-empty", text: card ? "Ajouter un champion" : "Choisis d'abord un joueur" }));
          }
          li.appendChild(btn);
          if (champ) {
            li.appendChild(el("button", {
              type: "button", class: "cs-clear", "aria-label": "Retirer " + champ.name, text: "×",
              onclick: function () { st.champions[index] = null; changed(); },
            }));
          }
          list.appendChild(li);
        })(i);
      }
      lane.appendChild(list);
      lanesEl.appendChild(lane);
    });
  }

  // ------------------------------------------------------------------ Compositions
  function renderComps() {
    compsEl.textContent = "";
    compScoresEl.textContent = "";
    var comps = result ? result.compositions : [];
    for (var i = 0; i < N; i++) {
      var comp = comps[i] || { index: i + 1, complete: false };
      var row = el("article", { class: "comp" + (comp.complete ? " is-complete" : "") });
      var icons = el("div", { class: "comp-champs" });
      data.roles.forEach(function (role) {
        var cid = state[role].champions[i];
        var champ = cid ? champions[cid] : null;
        icons.appendChild(champ
          ? el("img", { src: champ.portrait, alt: champ.name, title: role + " : " + champ.name, loading: "lazy" })
          : el("span", { class: "comp-hole", title: role + " : à choisir" }));
      });
      row.appendChild(el("h3", { text: "Compo " + (i + 1) }));
      row.appendChild(icons);
      if (comp.complete) {
        var checks = el("ul", { class: "checks" });
        comp.coherence.checks.forEach(function (c) {
          checks.appendChild(el("li", { class: "check is-" + c.state, title: c.detail }, [
            el("span", { text: c.label }), el("b", { text: fmt(c.points) + "/" + c.max }),
            el("small", { text: c.detail }),
          ]));
        });
        row.appendChild(el("div", { class: "comp-coherence" }, [
          el("div", { class: "ring", style: "--p:" + comp.coherence.score }, [el("b", { text: fmt(comp.coherence.score) }), el("small", { text: "cohérence" })]),
          checks,
        ]));
        row.appendChild(el("div", { class: "comp-score" }, [
          el("small", { text: "postes " + fmt(comp.average) }),
          el("b", { text: fmt(comp.score) }),
        ]));
      } else {
        var missing = data.roles.filter(function (role) { return !state[role].champions[i]; }).length;
        row.appendChild(el("p", { class: "comp-missing muted", text: missing ? missing + " champion" + (missing > 1 ? "s" : "") + " à choisir" : "Choisis les 5 joueurs" }));
      }
      compsEl.appendChild(row);
      compScoresEl.appendChild(el("li", { class: comp.complete ? "" : "is-empty" }, [
        el("span", { text: "C" + (i + 1) }), el("b", { text: comp.complete ? fmt(comp.score) : "—" }),
      ]));
    }
    var complete = result && result.complete;
    totalEl.textContent = complete ? fmt(result.score) : "—";
    totalEl.parentNode.classList.toggle("is-complete", !!complete);
    var fan = result && result.supporter && result.supporter.players ? result.supporter : null;
    if (complete) totalLabel.textContent = "score de l'équipe : la moyenne des 5 compositions" + (fan ? ", +" + fan.bonus + " % équipe de cœur (" + fan.team + ")" : "");
    else {
      var filled = 0;
      data.roles.forEach(function (role) { filled += state[role].champions.filter(Boolean).length; });
      totalLabel.textContent = filled + " / 25 champions choisis";
    }
    errorsEl.textContent = "";
    (result ? result.errors : []).forEach(function (e) { errorsEl.appendChild(el("li", { text: e })); });
  }

  // ------------------------------------------------------------------ Aperçu serveur
  var timer = null;
  var seq = 0;
  function picks() {
    var out = {};
    data.roles.forEach(function (role) {
      if (state[role].card) out[role] = { card: state[role].card, champions: state[role].champions };
    });
    return out;
  }
  function preview() {
    var mine = ++seq;
    fetch(data.previewUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify({ picks: picks(), week: !!data.week }),
      credentials: "same-origin",
    })
      .then(function (r) { return r.json(); })
      .then(function (json) {
        if (mine !== seq) return;
        result = json;
        renderLanes();
        renderComps();
      })
      .catch(function () {});
  }
  function changed() {
    renderLanes();
    renderComps();
    clearTimeout(timer);
    timer = setTimeout(preview, 200);
  }

  // ------------------------------------------------------------------ Sélecteurs
  var pickerMode = null;
  function openPicker(title, mode) {
    pickerMode = mode;
    pickerTitle.textContent = title;
    pickerSearch.value = "";
    renderPicker();
    if (picker.showModal) picker.showModal(); else picker.setAttribute("open", "");
    pickerSearch.focus();
  }
  function closePicker() { if (picker.close) picker.close(); else picker.removeAttribute("open"); }

  function openPlayers(role) {
    pickerPlayedWrap.hidden = true;
    var rule = rules[role];
    pickerNote.textContent = "Le bonus de rareté vient de ton meilleur exemplaire du joueur." +
      (rule && rule.on_card ? " Méta de la semaine : " + rule.label + " (+" + rule.bonus + " %)." : "") +
      (data.supporter ? " ♥ : joueur de " + data.supporter + " (+1 % au total)." : "");
    openPicker("Choisir un " + role, { kind: "player", role: role });
  }
  function openChampions(role, index) {
    pickerPlayedWrap.hidden = false;
    var card = cards[state[role].card];
    var crule = rules[role];
    pickerNote.textContent = card.ign + " : note sur les champions joués (la période compte pleinement, l'historique d'avant à moitié). Un champion jamais joué en compétition vaut sa note moins " + data.penalty + "." +
      (crule && !crule.on_card ? " Méta de la semaine : " + crule.label + "." : "");
    openPicker(card.ign + ", composition " + (index + 1), { kind: "champion", role: role, index: index });
  }

  function renderPicker() {
    pickerGrid.textContent = "";
    var q = pickerSearch.value.trim().toLowerCase();
    var mode = pickerMode;
    if (!mode) return;
    if (mode.kind === "player") {
      var used = {};
      data.roles.forEach(function (r) { if (r !== mode.role && state[r].card) used[cards[state[r].card].ign] = true; });
      (data.cards[mode.role] || []).filter(function (c) {
        return !q || (c.ign + " " + c.team + " " + c.split).toLowerCase().indexOf(q) >= 0;
      }).sort(function (a, b) { return cardMeta(b) - cardMeta(a); }).forEach(function (c) {
        var taken = used[c.ign];
        pickerGrid.appendChild(el("button", {
          type: "button", class: "pick-player" + (state[mode.role].card === c.id ? " is-current" : ""),
          style: "--rarity:" + c.rarity.color, disabled: taken ? "disabled" : null,
          title: taken ? c.ign + " joue déjà à un autre poste" : "",
          onclick: function () { state[mode.role].card = c.id; closePicker(); changed(); },
        }, [
          el("span", { class: "pp-photo" }, [c.photo ? el("img", { src: c.photo, alt: "", loading: "lazy" }) : el("span", { text: c.ign.slice(0, 2) })]),
          el("span", { class: "pp-text" }, [
            el("b", { text: c.ign + (c.fan ? " ♥" : "") }), el("small", { text: c.team + " · " + c.split }),
            el("span", { class: "pp-line" }, [
              el("span", { class: "pp-rating", text: fmt(c.rating) }),
              el("span", { class: "pp-rarity", text: c.rarity.name + (c.rarity.bonus ? " +" + fmt(c.rarity.bonus) + " %" : "") }),
              bonusTag(cardMeta(c)),
            ]),
          ]),
        ]));
      });
      if (!pickerGrid.children.length) pickerGrid.appendChild(el("p", { class: "muted", text: "Aucun joueur ne correspond." }));
      return;
    }
    var st = state[mode.role];
    var cardId = st.card;
    var mine = {};
    st.champions.forEach(function (cid, i) { if (cid && i !== mode.index) mine[cid] = i + 1; });
    // Fearless : un champion ne sert qu'une fois dans toute l'équipe.
    var inComp = {};
    data.roles.forEach(function (r) {
      if (r === mode.role) return;
      state[r].champions.forEach(function (cid, i) {
        if (cid) inComp[cid] = r + (i === mode.index ? " dans cette compo" : " en compo " + (i + 1));
      });
    });
    var onlyPlayed = pickerPlayed.checked && !q;
    var list = data.champions.map(function (c) { return { c: c, r: rating(cardId, c.id) }; })
      .filter(function (x) { return (!onlyPlayed || x.r.played) && (!q || x.c.name.toLowerCase().indexOf(q) >= 0); })
      .sort(function (a, b) { return (b.r.played - a.r.played) || (b.r.rating - a.r.rating) || a.c.name.localeCompare(b.c.name); });
    list.forEach(function (x) {
      var c = x.c, r = x.r;
      var blocked = mine[c.id] ? "déjà en compo " + mine[c.id] : (inComp[c.id] ? "fearless : pris par le " + inComp[c.id] : "");
      var games = r.played ? (r.games ? r.games + " partie" + (r.games > 1 ? "s" : "") : "") + (r.before ? (r.games ? " · " : "") + r.before + " avant" : "") : "jamais joué";
      pickerGrid.appendChild(el("button", {
        type: "button", class: "pick-champ" + (st.champions[mode.index] === c.id ? " is-current" : "") + (r.played ? " is-played" : ""),
        disabled: blocked ? "disabled" : null, title: blocked || c.title || "",
        onclick: function () { st.champions[mode.index] = c.id; closePicker(); changed(); },
      }, [
        el("img", { src: c.portrait, alt: "", loading: "lazy" }),
        el("span", { class: "pc-rating", text: fmt(r.rating) }),
        bonusTag(champMeta(mode.role, c.id)),
        el("span", { class: "pc-text" }, [el("b", { text: c.name }), el("small", { text: blocked || games })]),
      ]));
    });
    if (!list.length) {
      pickerGrid.appendChild(el("p", { class: "muted", text: onlyPlayed ? "Aucun champion joué : décoche « Seulement ses champions » pour voir tous les autres." : "Aucun champion ne correspond." }));
    }
  }

  pickerSearch.addEventListener("input", renderPicker);
  pickerPlayed.addEventListener("change", renderPicker);
  document.getElementById("picker-close").addEventListener("click", closePicker);
  picker.addEventListener("click", function (e) { if (e.target === picker) closePicker(); });

  form.addEventListener("submit", function () {
    document.getElementById("picks-input").value = JSON.stringify(picks());
  });

  renderLanes();
  renderComps();
  preview();
})();

// Le Bo5 du mode tournoi, sur une seule page.
//
// La page arrive avec deux blocs de données : l'état du Bo5 (qui change à
// chaque coup) et ce qui ne change pas (les champions, les notes de tes
// joueurs, ce que l'adversaire a joué). Chaque coup (choix des joueurs,
// ban, pick) part en JSON ; le serveur renvoie le nouvel état et la page
// se redessine, sans rechargement.
(function () {
  "use strict";
  var root = document.getElementById("bo5");
  if (!root) return;
  var state = JSON.parse(document.getElementById("bo5-state").textContent);
  var fixed = JSON.parse(document.getElementById("bo5-static").textContent);
  var champions = {};
  var ui = { role: null, search: "", onlyPlayed: false, selected: null, busy: false, error: "" };
  index();

  function index() {
    champions = {};
    fixed.champions.forEach(function (c) { champions[c.id] = c; });
  }

  // ---------------------------------------------------------------- outils
  function h(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), v);
      else if (k === "style") node.setAttribute("style", v);
      else node.setAttribute(k, v === true ? "" : v);
    });
    (children || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function plural(n, word) { return n + " " + word + (n > 1 ? "s" : ""); }

  function format(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    var hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
    if (hh) return hh + " h " + String(mm).padStart(2, "0");
    if (mm) return mm + " min " + String(ss).padStart(2, "0");
    return ss + " s";
  }

  function countdown(iso) {
    var node = h("time", { datetime: iso });
    function tick() {
      var left = new Date(iso).getTime() - Date.now();
      node.textContent = format(left);
      if (left > 0 && node.isConnected !== false) setTimeout(tick, 1000);
    }
    tick();
    return node;
  }

  function miniCard(card, extra) {
    if (!card) return h("div", { class: "mini-card is-empty" }, [h("span", { text: "?" })]);
    var r = card.rarity || { color: "#5a6478", name: "" };
    return h("div", { class: "mini-card", style: "--rarity:" + r.color }, [
      h("div", { class: "mc-photo" }, [
        card.photo ? h("img", { src: card.photo, alt: "", loading: "lazy" }) : h("span", { class: "nophoto", text: card.ign.slice(0, 2) }),
        h("span", { class: "mc-ovr", text: String(card.overall) }),
      ]),
      h("b", { class: "mc-name", text: card.ign }),
      h("small", { text: card.role + (r.name ? " · " + r.name : "") }),
      extra || null,
    ]);
  }

  function champ(id) { return champions[id] || { id: id, name: "?", icon: "" }; }

  // ---------------------------------------------------------------- envoi
  function send(payload) {
    if (ui.busy) return;
    ui.busy = true;
    ui.error = "";
    render();
    fetch(root.dataset.action, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": root.dataset.csrf },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        if (res.data.state) state = res.data.state;
        if (res.data.static) { fixed = res.data.static; index(); }
        if (!res.ok) ui.error = res.data.error || "Coup refusé.";
        ui.selected = null;
        if (res.data.played) {
          ui.role = null;
          ui.flash = res.data.played;
        }
      })
      .catch(function () { ui.error = "Connexion perdue : réessaie."; })
      .then(function () { ui.busy = false; render(); });
  }

  // ---------------------------------------------------------------- rendu
  function header() {
    return h("div", { class: "bo5-head" }, [
      h("div", {}, [
        h("h1", { text: "Bo5 contre " + state.team }),
        h("p", { class: "muted" }, [
          "Adversaire " + state.step + " sur " + state.total + " · ses cartes sont ",
          h("b", { style: "color:" + state.opponent_rarity.color, text: state.opponent_rarity.name.toLowerCase() + "s" }),
          " · premier à 3 victoires",
        ]),
      ]),
      h("div", { class: "bo5-score", "aria-label": "Score du Bo5" }, [
        h("b", { class: state.wins > state.losses ? "lead" : "", text: String(state.wins) }),
        h("span", { text: "–" }),
        h("b", { class: state.losses > state.wins ? "lead" : "", text: String(state.losses) }),
      ]),
    ]);
  }

  function result() {
    if (state.status === "won") {
      return h("div", { class: "bo5-result won" }, [
        h("div", {}, [
          h("h2", { text: "Victoire " + state.wins + "–" + state.losses }),
          h("p", { text: state.reward ? state.reward.ign + " rejoint ta collection en " + state.reward.rarity.name.toLowerCase() + "." : "" }),
          state.next_at ? h("p", { class: "muted" }, ["Adversaire suivant dans ", countdown(state.next_at)]) : null,
          h("p", {}, [h("a", { class: "btn btn-gold", href: root.dataset.back, text: "Retour au tournoi" })]),
        ]),
        state.reward ? miniCard(state.reward) : null,
      ]);
    }
    if (state.status === "lost") {
      return h("div", { class: "bo5-result lost" }, [
        h("div", {}, [
          h("h2", { text: "Défaite " + state.wins + "–" + state.losses }),
          h("p", {}, state.next_at
            ? ["Tu n'es pas éliminé : retente dans ", countdown(state.next_at), ", avec d'autres joueurs ou d'autres champions."]
            : ["Tu n'es pas éliminé : tu peux retenter dès maintenant."]),
          h("p", {}, [h("a", { class: "btn btn-gold", href: root.dataset.back, text: "Retour au tournoi" })]),
        ]),
      ]);
    }
    return null;
  }

  function lineup() {
    var picks = {};
    var cols = fixed.roles.map(function (role, i) {
      var options = (fixed.choices[role] || []).map(function (c) {
        return h("option", { value: c.id, text: c.ign + " · " + c.overall + " · " + c.rarity.name.toLowerCase() + " · " + c.split });
      });
      var select = h("select", { name: role, required: true, onchange: function () { picks[role] = this.value; } },
        [h("option", { value: "", text: "— choisir —" })].concat(options));
      return h("div", { class: "lineup-col" }, [
        miniCard(state.opponent[i]),
        h("label", { class: "lineup-pick" }, [
          h("span", { text: "Ton " + role }), select,
          options.length ? null : h("small", { class: "muted", text: "Aucun " + role + " dans ta collection : ouvre des boosters." }),
        ]),
      ]);
    });
    return h("div", {}, [
      h("div", { class: "strap" }, [h("h2", { text: "Choisis tes 5 joueurs" })]),
      h("p", { class: "muted", text: "Face à chaque joueur adverse, aligne un joueur de ta collection au même poste. Ses notes sont celles de ce tournoi ; les tiennes, celles de ta carte, avec le bonus de sa meilleure rareté." }),
      h("form", {
        class: "lineup-form", onsubmit: function (e) {
          e.preventDefault();
          fixed.roles.forEach(function (r) { picks[r] = this.elements[r].value; }, this);
          send({ type: "lineup", picks: picks });
        },
      }, cols.concat([h("div", { class: "lineup-submit" }, [
        h("button", { class: "btn btn-gold btn-big", type: "submit", disabled: ui.busy, text: ui.busy ? "…" : "Valider mes joueurs" }),
      ])])),
    ]);
  }

  function side(team, cards, label, color) {
    var d = state.draft;
    return h("section", { class: "draft-side " + color }, [
      h("h3", {}, [label + " ", h("small", { text: "côté " + (color === "blue" ? "bleu" : "rouge") })]),
      h("div", { class: "draft-bans" }, d.bans[team].length
        ? d.bans[team].map(function (id) { return h("span", { class: "ban", text: champ(id).name }); })
        : [h("span", { class: "muted small", text: "pas de ban" })]),
    ].concat(fixed.roles.map(function (role, i) {
      var id = d.picks[team][role];
      return h("div", { class: "draft-slot" + (id ? " is-filled" : "") }, [
        h("span", { class: "role role-" + role.toLowerCase(), text: role }),
        h("b", { text: cards[i] ? cards[i].ign : "" }),
        h("span", { class: "draft-champ" }, id
          ? [h("img", { src: champ(id).icon, alt: "", class: "champ-icon small" }), champ(id).name]
          : ["…"]),
      ]);
    })));
  }

  function ratingFor(role, id) {
    var r = (fixed.ratings || {})[role];
    if (!r) return { rating: 0, games: 0, played: false };
    var p = r.played[id];
    return p ? { rating: p[0], games: p[1], played: true } : { rating: r.base, games: 0, played: false };
  }

  function hintFor(id) {
    var list = (fixed.hints || {})[id] || [];
    var total = 0;
    list.forEach(function (x) { total += x[1]; });
    return { total: total, text: list.map(function (x) { return x[0] + " ×" + x[1]; }).join(", ") };
  }

  function turnPanel() {
    var d = state.draft, turn = d.turn;
    if (!turn || turn.team !== "user") return h("p", { class: "muted", text: "L'adversaire réfléchit…" });
    var isPick = turn.type === "pick";
    var free = fixed.champions.filter(function (c) { return d.taken.indexOf(c.id) < 0; });
    var openRoles = fixed.roles.filter(function (r) { return !d.picks.user[r]; });
    if (isPick && openRoles.indexOf(ui.role) < 0) ui.role = openRoles[0];
    var q = ui.search.trim().toLowerCase();

    var tiles = free.map(function (c) {
      var info = isPick ? ratingFor(ui.role, c.id) : null;
      var hint = isPick ? null : hintFor(c.id);
      return { c: c, info: info, hint: hint, played: isPick ? info.played : hint.total > 0 };
    }).filter(function (t) {
      if (q) return t.c.name.toLowerCase().indexOf(q) >= 0;
      return !ui.onlyPlayed || t.played;
    });
    tiles.sort(function (a, b) {
      if (isPick) return (b.info.played - a.info.played) || (b.info.rating - a.info.rating) || a.c.name.localeCompare(b.c.name);
      return (b.hint.total - a.hint.total) || a.c.name.localeCompare(b.c.name);
    });

    var grid = h("div", { class: "champ-grid", role: "listbox", "aria-label": "Champions" }, tiles.map(function (t) {
      var note = isPick ? String(t.info.rating) : (t.hint.total ? "×" + t.hint.total : "");
      var title = isPick ? (t.info.played ? plural(t.info.games, "partie") : "jamais joué") : (t.hint.text ? "Joué par " + t.hint.text : "");
      return h("button", {
        type: "button", role: "option", title: title || null,
        class: "champ-tile" + (t.played ? " is-played" : "") + (ui.selected === t.c.id ? " is-selected" : ""),
        "aria-selected": ui.selected === t.c.id ? "true" : "false",
        onclick: function () { ui.selected = t.c.id; render(); },
        ondblclick: function () { ui.selected = t.c.id; confirm(); },
      }, [
        h("img", { src: t.c.icon, alt: "", loading: "lazy", width: "52", height: "52" }),
        h("span", { class: "ct-name", text: t.c.name }),
        note ? h("span", { class: "ct-note", text: note }) : null,
      ]);
    }));
    if (!tiles.length) grid.appendChild(h("p", { class: "muted", text: "Aucun champion ne correspond." }));

    function confirm() {
      if (!ui.selected) return;
      send(isPick ? { type: "pick", champion: ui.selected, role: ui.role } : { type: "ban", champion: ui.selected });
    }

    var chosen = ui.selected ? champ(ui.selected).name : null;
    return h("div", { class: "draft-turn", "data-kind": turn.type }, [
      h("p", { class: "draft-step" }, ["Étape " + d.step + " sur " + d.steps + " : ",
        h("b", { text: isPick ? "à toi de choisir un champion pour un de tes joueurs" : "à toi de bannir un champion" })]),
      isPick ? h("div", { class: "draft-roles", role: "tablist", "aria-label": "Pour quel joueur" }, openRoles.map(function (role) {
        var i = fixed.roles.indexOf(role);
        return h("button", {
          type: "button", role: "tab", class: "draft-role" + (role === ui.role ? " is-active" : ""),
          "aria-selected": role === ui.role ? "true" : "false",
          onclick: function () { ui.role = role; render(); },
        }, [h("span", { class: "role role-" + role.toLowerCase(), text: role }), " " + state.lineup[i].ign]);
      })) : null,
      h("div", { class: "draft-tools" }, [
        h("input", {
          type: "search", placeholder: "Chercher un champion", "aria-label": "Chercher un champion", value: ui.search,
          oninput: function () { ui.search = this.value; renderKeepFocus("search"); }, "data-focus": "search",
        }),
        h("label", { class: "small" }, [
          h("input", { type: "checkbox", checked: ui.onlyPlayed, onchange: function () { ui.onlyPlayed = this.checked; render(); } }),
          isPick ? " Seulement ses champions" : " Seulement ceux joués en face",
        ]),
      ]),
      grid,
      h("div", { class: "draft-confirm" }, [
        h("span", { class: "muted", text: chosen ? (isPick ? chosen + " pour ton " + ui.role : "Bannir " + chosen) : "Choisis un champion dans la grille (double-clic pour valider)." }),
        h("button", {
          class: "btn btn-gold", type: "button", disabled: !ui.selected || ui.busy, onclick: confirm,
          text: ui.busy ? "…" : (isPick ? "Choisir" : "Bannir"),
        }),
      ]),
    ]);
  }

  function draft() {
    var d = state.draft;
    var user = d.side, them = d.side === "blue" ? "red" : "blue";
    return h("div", {}, [
      h("div", { class: "strap", id: "draft" }, [h("h2", { text: "Partie " + (state.games.length + 1) + " : draft" })]),
      h("p", { class: "muted", text: "Draft de tournoi à tour de rôle : 3 bans, 3 picks, puis 2 bans et 2 picks de chaque côté. Fearless : un champion joué dans ce Bo5 ne revient plus, pour personne. Chaque poste compte la note du joueur, sa note sur le champion, le bonus de rareté et le bilan du champion face à celui d'en face." }),
      state.fearless.length ? h("p", { class: "fearless" }, [h("b", { text: "Déjà joués : " })].concat(
        state.fearless.map(function (id) { return h("span", { class: "banned", text: champ(id).name }); }))) : null,
      h("div", { class: "draft-board" }, [
        side("user", state.lineup, "Toi", user),
        side("opponent", state.opponent, state.team, them),
      ]),
      turnPanel(),
    ]);
  }

  function games() {
    if (!state.games.length) return null;
    return h("div", {}, [h("div", { class: "strap" }, [h("h2", { text: "Parties" })])].concat(state.games.map(function (g) {
      return h("article", { class: "game-card " + (g.won ? "won" : "lost") + (ui.flash === g.number ? " is-new" : ""), id: "partie-" + g.number }, [
        h("header", {}, [
          h("h3", { text: "Partie " + g.number + " : " + (g.won ? "gagnée" : "perdue") }),
          h("span", { class: "game-total" }, [h("b", { text: String(g.user_score) }), " – ", h("b", { text: String(g.opponent_score) })]),
        ]),
        h("table", { class: "game-table" }, [
          h("thead", {}, [h("tr", {}, ["Toi", "Score", "Face-à-face", "Score", state.team].map(function (t) { return h("th", { scope: "col", text: t }); }))]),
          h("tbody", {}, g.rows.map(function (r) {
            var mine = r.user, theirs = r.opponent;
            return h("tr", {}, [
              h("td", {}, [h("b", { text: mine.champion_name }), h("small", { class: "muted", text: mine.player + " · " + r.role })]),
              h("td", { class: "num", text: String(mine.score) }),
              h("td", { class: "face" }, [
                h("span", { class: mine.bonus > 0 ? "up" : (mine.bonus < 0 ? "down" : ""), text: mine.matchup + " %" }),
                h("small", { class: "muted", text: mine.matchup_games ? plural(mine.matchup_games, "partie") : "jamais vu" }),
              ]),
              h("td", { class: "num", text: String(theirs.score) }),
              h("td", {}, [h("b", { text: theirs.champion_name }), h("small", { class: "muted", text: theirs.player })]),
            ]);
          }).concat([h("tr", { class: "coherence" }, [
            h("td", { text: "Cohérence de la compo" }), h("td", { class: "num", text: String(g.user_coherence) }),
            h("td"), h("td", { class: "num", text: String(g.opponent_coherence) }), h("td"),
          ])])),
        ]),
      ]);
    })));
  }

  function render() {
    var scroll = root.querySelector(".champ-grid");
    var top = scroll ? scroll.scrollTop : 0;
    var parts = [header()];
    if (ui.error) parts.push(h("p", { class: "flash flash-error", role: "alert", text: ui.error }));
    parts.push(result());
    if (state.status === "lineup") parts.push(lineup());
    if (state.status === "draft" && state.draft) parts.push(draft());
    parts.push(games());
    root.replaceChildren.apply(root, parts.filter(Boolean));
    var grid = root.querySelector(".champ-grid");
    if (grid && !ui.flash) grid.scrollTop = top;
    if (ui.flash) {
      var played = document.getElementById("partie-" + ui.flash);
      ui.flash = null;
      if (played) played.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function renderKeepFocus(name) {
    var input = root.querySelector("[data-focus=" + name + "]");
    var pos = input ? input.selectionStart : null;
    render();
    var again = root.querySelector("[data-focus=" + name + "]");
    if (again) { again.focus(); if (pos !== null) again.setSelectionRange(pos, pos); }
  }

  render();
})();

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
  // L'animation : les coups arrivent tous d'un coup du serveur, la page les
  // rejoue un par un (DELAY entre deux coups adverses, comme une vraie draft).
  var DUEL = root.dataset.mode === "duel";
  // En 1V1, les coups d'en face ont déjà pris leur temps : on les rejoue vite.
  var DELAY = DUEL ? 700 : 5000, FAST = DUEL ? 400 : 1200;
  var view = null;        // la draft affichée pendant une animation
  var queue = [];         // les étapes restant à jouer
  var auto = { on: false, timer: null };
  try { auto.on = localStorage.getItem("lg-auto") === "1"; ui.fast = localStorage.getItem("lg-fast") === "1"; } catch (e) { /* stockage indisponible */ }
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
  function signed(n) { return (n > 0 ? "+" : "") + n; }
  // Le détail des points de draft d'un poste (survol).
  function draftTitle(mine, theirs) {
    function one(s) {
      var d = s.draft;
      if (!d) return s.champion_name + " : " + signed(s.bonus);
      return s.champion_name + " : " + signed(s.bonus) + " pts (bilan pro " + d.rate + " %, synergies " +
        signed(d.synergy) + ", face-à-face " + signed(d.matchup) + ")";
    }
    return one(mine) + "\n" + one(theirs);
  }

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
        var before = state;
        if (res.data.static) { fixed = res.data.static; index(); }
        if (!res.ok) ui.error = res.data.error || "Coup refusé.";
        ui.selected = null;
        if (res.data.state) {
          if (res.ok && before.status === "draft") {
            try { replay(before, res.data.state, res.data.played); } catch (e) { queue = []; view = null; state = res.data.state; }
          } else state = res.data.state;
        }
      })
      .catch(function () { ui.error = "Connexion perdue : réessaie."; })
      .then(function () { ui.busy = false; if (!queue.length) render(); });
  }

  // ---------------------------------------------------------------- rendu
  function header() {
    if (DUEL) {
      var live = state.raw_status === "lineup" || state.raw_status === "draft";
      return h("div", { class: "bo5-head" }, [
        h("div", {}, [
          h("h1", { text: "Duel contre " + state.team }),
          h("p", { class: "muted" }, ["Premier à 3 victoires · méta tirée pour ce duel",
            state.opponent_auto && live ? " · " + state.team + " est en mode auto" : ""]),
        ]),
        h("div", { class: "bo5-score", "aria-label": "Score du duel" }, [
          h("b", { class: state.wins > state.losses ? "lead" : "", text: String(state.wins) }),
          h("span", { text: "–" }),
          h("b", { class: state.losses > state.wins ? "lead" : "", text: String(state.losses) }),
        ]),
      ]);
    }
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
    if (DUEL) {
      if (state.status !== "won" && state.status !== "lost") return null;
      var won = state.status === "won";
      return h("div", { class: "bo5-result " + (won ? "won" : "lost") }, [h("div", {}, [
        h("h2", { text: (won ? "Victoire " : "Défaite ") + state.wins + "–" + state.losses }),
        state.forfeit ? h("p", { text: won ? state.team + " a abandonné." : "Tu as abandonné." }) : null,
        h("p", {}, [h("a", { class: "btn btn-gold", href: root.dataset.back, text: "Retour au 1V1" })]),
      ])]);
    }
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

  function duelDeadline(text) {
    return state.deadline ? h("p", { class: "duel-clock" }, [text + " ", countdown(state.deadline)]) : null;
  }

  function lineup() {
    if (DUEL && state.lineup_done) {
      return h("div", { class: "duel-wait" }, [
        h("div", { class: "strap" }, [h("h2", { text: "Tes joueurs sont prêts" })]),
        h("p", { class: "muted", text: "En attente de " + state.team + " : vous découvrirez vos alignements en même temps." }),
        duelDeadline("Choix automatique dans"),
        h("div", { class: "lineup-row" }, state.lineup.map(function (c) { return miniCard(c); })),
      ]);
    }
    var picks = {};
    var cols = fixed.roles.map(function (role, i) {
      var options = (fixed.choices[role] || []).map(function (c) {
        return h("option", { value: c.id, text: (c.meta ? "★ +" + c.meta + " % · " : "") + c.ign + " · " + c.overall + " · " + c.rarity.name.toLowerCase() + " · " + c.split });
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
      h("p", { class: "muted", text: DUEL
        ? "Aligne un joueur de ta collection à chaque poste. Ceux marqués ★ remplissent la méta du duel. Les joueurs d'en face restent cachés jusqu'à ce que vous ayez validé tous les deux."
        : "Face à chaque joueur adverse, aligne un joueur de ta collection au même poste. Ses notes sont celles de ce tournoi ; les tiennes, celles de ta carte, avec le bonus de sa meilleure rareté." }),
      DUEL ? duelDeadline("Il te reste") : null,
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

  // Les bans et picks d'une draft, à partir de la liste de ses coups.
  function board(d) {
    var out = { bans: { user: [], opponent: [] }, picks: { user: {}, opponent: {} } };
    (d.actions || []).forEach(function (a) {
      if (a.type === "ban") out.bans[a.team].push(a.champion);
      else out.picks[a.team][a.role] = a.champion;
    });
    return out;
  }

  function side(b, team, cards, label, color, last) {
    var waiting = view && view.waiting && view.waiting.team === team;
    return h("section", { class: "draft-side " + color + (waiting ? " is-thinking" : "") }, [
      h("h3", {}, [label + " ", h("small", { text: "côté " + (color === "blue" ? "bleu" : "rouge") })]),
      h("div", { class: "draft-bans" }, b.bans[team].length
        ? b.bans[team].map(function (id) {
          var fresh = last && last.type === "ban" && last.team === team && last.champion === id;
          return h("span", { class: "ban" + (fresh ? " is-fresh" : "") }, [
            champ(id).icon ? h("img", { src: champ(id).icon, alt: "", class: "champ-icon tiny" }) : null, champ(id).name]);
        })
        : [h("span", { class: "muted small", text: "pas de ban" })]),
    ].concat(fixed.roles.map(function (role, i) {
      var id = b.picks[team][role];
      var fresh = last && last.type === "pick" && last.team === team && last.role === role;
      return h("div", { class: "draft-slot" + (id ? " is-filled" : "") + (fresh ? " is-fresh" : "") }, [
        h("span", { class: "role role-" + role.toLowerCase(), text: role }),
        h("b", { text: cards[i] ? cards[i].ign : "" }),
        h("span", { class: "draft-champ" }, id
          ? [h("img", { src: champ(id).icon, alt: "", class: "champ-icon small" }), champ(id).name]
          : ["…"]),
      ]);
    })));
  }

  function specialPanel(special) {
    if (!special) return null;
    function line(side, who) {
      if (!side) return null;
      return h("div", { class: "special-side" }, [
        h("p", {}, [h("b", { text: who }), " : la forme de " + side.player + " (" + side.role + ")" + (side.year ? ", en " + side.year : "")]),
        h("div", { class: "special-list" }, side.effects.map(function (e) {
          return h("span", { class: "special-effect " + (e.points > 0 ? "up" : (e.points < 0 ? "down" : "")) }, [
            champ(e.champion).icon ? h("img", { src: champ(e.champion).icon, alt: "", class: "champ-icon tiny" }) : null,
            e.name + " ", h("b", { text: (e.points > 0 ? "+" : "") + e.points }),
            h("small", { class: "muted", text: " · " + e.rate + " % sur " + plural(e.games, "partie") }),
          ]);
        })),
      ]);
    }
    return h("div", { class: "special" }, [
      h("p", {}, [h("b", { text: "Particularité de la partie : " }),
        "dans chaque équipe, un joueur en vedette dont les champions préférés gagnent ou perdent des points selon leur bilan " +
        (special.year ? "en " + special.year : "l'année de sa carte") + ". Elle compte pour toi comme pour l'adversaire."]),
      line(special.user, "Chez toi"),
      line(special.opponent, "Chez " + state.team),
    ]);
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

  function autoPanel() {
    return h("div", { class: "auto-mode" }, [
      h("label", {}, [
        h("input", { type: "checkbox", checked: auto.on, onchange: function () {
          auto.on = this.checked;
          try { localStorage.setItem("lg-auto", auto.on ? "1" : "0"); } catch (e) { /* rien */ }
          render();
        } }),
        h("b", { text: " Mode auto" }),
      ]),
      h("p", { class: "small muted", text: "Il joue à ta place, simplement : pour un pick, le champion libre le mieux noté d'un de tes joueurs ; pour un ban, le champion que l'adversaire a le plus joué. Il ne regarde ni les face-à-face, ni la cohérence de la compo, ni la particularité de la partie" + (DUEL ? ", ni la méta du duel" : "") + " : à toi de faire mieux !" }),
      h("label", { class: "small" }, [
        h("input", { type: "checkbox", checked: !!ui.fast, onchange: function () {
          ui.fast = this.checked;
          try { localStorage.setItem("lg-fast", ui.fast ? "1" : "0"); } catch (e) { /* rien */ }
        } }),
        " Animation rapide",
      ]),
    ]);
  }

  function turnPanel() {
    var d = state.draft, turn = d.turn;
    if (!turn || turn.team !== "user") {
      if (DUEL) return h("div", { class: "draft-turn is-waiting" }, [
        h("p", { class: "draft-step" }, [h("b", { text: state.team + (turn && turn.type === "ban" ? " bannit…" : " choisit…") })]),
        duelDeadline(state.opponent_auto ? "Mode auto" : "Coup automatique dans"),
      ]);
      return h("p", { class: "muted", text: "L'adversaire réfléchit…" });
    }
    if (DUEL && state.auto_forced) {
      return h("div", { class: "draft-turn is-auto" }, [
        h("p", { class: "draft-step" }, [h("b", { text: "Tu as manqué 3 tours : le mode auto joue pour toi jusqu'à la fin du duel." })]),
      ]);
    }
    if (auto.on) {
      return h("div", { class: "draft-turn is-auto" }, [
        h("p", { class: "draft-step" }, ["Étape " + d.step + " sur " + d.steps + " : ",
          h("b", { text: "le mode auto " + (turn.type === "ban" ? "bannit" : "choisit") + " pour toi…" })]),
        DUEL ? duelDeadline("Fin du tour dans") : null,
        h("div", { class: "timer", style: "--delay:" + delay() + "ms" }, [h("span")]),
      ]);
    }
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
      var mineSp = d.special && d.special.user;
      var sp = isPick && mineSp && mineSp.role === ui.role
        ? mineSp.effects.filter(function (e) { return e.champion === t.c.id; })[0] : null;
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
        sp ? h("span", { class: "ct-special " + (sp.points >= 0 ? "up" : "down"), title: "Particularité de la partie",
          text: (sp.points > 0 ? "+" : "") + sp.points }) : null,
        metaFor(t.c.id) ? h("span", { class: "ct-meta", title: "Méta du duel", text: "+" + metaFor(t.c.id) + " %" }) : null,
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
      DUEL ? duelDeadline("Il te reste") : null,
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

  function metaFor(id) {
    if (!DUEL || !ui.role) return 0;
    var m = (fixed.metaChampions || {})[ui.role] || {};
    return m[id] || 0;
  }

  function metaPanel() {
    if (!DUEL || !state.meta || !state.meta.length) return null;
    return h("div", { class: "duel-meta" }, [
      h("p", {}, [h("b", { text: "Méta du duel" }), " : un bonus par poste, ajouté au bonus de rareté, pour les deux joueurs."]),
      h("ol", { class: "meta-rules is-compact" }, state.meta.map(function (r) {
        return h("li", { class: "meta-rule" }, [h("span", { class: "meta-role", text: r.role }),
          h("b", { class: "meta-bonus", text: (r.kind === "offmeta" ? "jusqu'à " : "") + "+" + r.bonus + " %" }),
          h("p", { text: r.label })]);
      })),
    ]);
  }

  function forfeitButton() {
    if (!DUEL || (state.raw_status !== "lineup" && state.raw_status !== "draft")) return null;
    return h("p", { class: "duel-forfeit" }, [h("button", { type: "button", class: "btn btn-small btn-ghost", text: "Abandonner le duel",
      onclick: function () { if (window.confirm("Abandonner ce duel ? " + state.team + " gagnera.")) send({ type: "forfeit" }); } })]);
  }

  function draft() {
    var replaying = !!view;
    var d = replaying ? view.draft : state.draft;
    var fearless = replaying ? view.fearless : state.fearless;
    var b = board(d);
    var them = d.side === "blue" ? "red" : "blue";
    var waiting = replaying && view.waiting;
    var number = state.games.length + (view && view.result ? 0 : 1);
    var status = null;
    if (replaying && view.result) {
      var g = view.result;
      status = h("div", { class: "draft-turn draft-over " + (g.won ? "won" : "lost") }, [
        h("p", { class: "draft-step" }, [h("b", { text: "Partie " + g.number + " : " + (g.won ? "gagnée" : "perdue") }),
          "  " + g.user_score + " – " + g.opponent_score]),
      ]);
    } else if (waiting) {
      var who = waiting.team === "user" ? (auto.on ? "Le mode auto" : "Toi") : state.team;
      status = h("div", { class: "draft-turn is-waiting" }, [
        h("p", { class: "draft-step" }, [h("b", { text: who + (waiting.type === "ban" ? " bannit…" : " choisit…") })]),
        waiting.team === "user" ? null : h("div", { class: "timer", style: "--delay:" + delay() + "ms" }, [h("span")]),
      ]);
    } else if (!replaying) {
      status = turnPanel();
    }
    return h("div", {}, [
      h("div", { class: "strap", id: "draft" }, [h("h2", { text: "Partie " + number + " : draft" })]),
      h("p", { class: "muted", text: "Draft de tournoi à tour de rôle : 3 bans, 3 picks, puis 2 bans et 2 picks de chaque côté. Fearless : un champion joué dans ce Bo5 ne revient plus, pour personne. Chaque poste compte la note du joueur, sa note sur le champion, le bonus de rareté, les points de draft (bilan pro du champion à son poste, ses synergies avec tes autres picks et ses face-à-face avec les cinq champions d'en face) et la particularité de la partie." }),
      specialPanel(d.special),
      fearless.length ? h("p", { class: "fearless" }, [h("b", { text: "Déjà joués : " })].concat(
        fearless.map(function (id) { return h("span", { class: "banned", text: champ(id).name }); }))) : null,
      h("div", { class: "draft-board" }, [
        side(b, "user", state.lineup, "Toi", d.side, replaying ? view.last : null),
        side(b, "opponent", state.opponent, state.team, them, replaying ? view.last : null),
      ]),
      status,
      replaying ? null : autoPanel(),
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
          h("thead", {}, [h("tr", {}, ["Toi", "Score", "Draft", "Score", state.team].map(function (t) { return h("th", { scope: "col", text: t }); }))]),
          h("tbody", {}, g.rows.map(function (r) {
            var mine = r.user, theirs = r.opponent;
            return h("tr", {}, [
              h("td", {}, [h("b", { text: mine.champion_name }), h("small", { class: "muted", text: mine.player + " · " + r.role })]),
              h("td", { class: "num" }, [String(mine.score), mine.special ? h("small", {
                class: mine.special > 0 ? "up" : "down", title: "Particularité de la partie",
                text: (mine.special > 0 ? "+" : "") + mine.special + " forme" }) : null]),
              h("td", { class: "face", title: draftTitle(mine, theirs) }, [
                h("span", { class: mine.bonus > theirs.bonus ? "up" : (mine.bonus < theirs.bonus ? "down" : "") },
                  [signed(mine.bonus) + " / " + signed(theirs.bonus)]),
                h("small", { class: "muted", text: "couloir " + mine.matchup + " % · " +
                  (mine.matchup_games ? plural(mine.matchup_games, "partie") : "jamais vu") }),
              ]),
              h("td", { class: "num" }, [String(theirs.score), theirs.special ? h("small", {
                class: theirs.special > 0 ? "up" : "down", title: "Particularité de la partie",
                text: (theirs.special > 0 ? "+" : "") + theirs.special + " forme" }) : null]),
              h("td", {}, [h("b", { text: theirs.champion_name }), h("small", { class: "muted", text: theirs.player })]),
            ]);
          }).concat(g.user_draft != null ? [h("tr", { class: "coherence" }, [
            h("td", { text: "Chances de la draft" }), h("td", { class: "num", text: g.user_draft + " %" }),
            h("td"), h("td", { class: "num", text: g.opponent_draft + " %" }), h("td"),
          ])] : []).concat([h("tr", { class: "coherence" }, [
            h("td", { text: "Cohérence de la compo" }), h("td", { class: "num", text: String(g.user_coherence) }),
            h("td"), h("td", { class: "num", text: String(g.opponent_coherence) }), h("td"),
          ])])),
        ]),
      ]);
    })));
  }

  // ------------------------------------------------------------ animation
  function delay() { return ui.fast ? FAST : DELAY; }

  function replay(before, after, played) {
    var known = (before.draft && before.draft.actions || []).length;
    var steps = [];
    if (played) {
      var game = after.games[0];
      var base = { side: game.side, special: game.special, actions: game.actions.slice(0, known) };
      var fearless = before.fearless;
      game.actions.slice(known).forEach(function (a) { steps.push({ kind: "action", action: a, draft: base, fearless: fearless }); });
      steps.push({ kind: "result", game: game });
      if (after.draft) {
        var nextDraft = { side: after.draft.side, special: after.draft.special, actions: [] };
        steps.push({ kind: "new", draft: nextDraft, fearless: after.fearless });
        after.draft.actions.forEach(function (a) { steps.push({ kind: "action", action: a, draft: nextDraft, fearless: after.fearless }); });
      }
    } else {
      var draftNow = { side: after.draft.side, special: after.draft.special, actions: before.draft.actions.slice() };
      after.draft.actions.slice(known).forEach(function (a) { steps.push({ kind: "action", action: a, draft: draftNow, fearless: after.fearless }); });
    }
    queue = steps;
    var final = after;
    view = null;
    next();

    function next() {
      if (!queue.length) {
        view = null;
        state = final;
        if (played) { ui.role = null; ui.flash = played; }
        render();
        return;
      }
      var step = queue.shift();
      if (step.kind === "new") {
        view = { draft: step.draft, fearless: step.fearless, waiting: null, last: null, result: null };
        render();
        return next();
      }
      if (step.kind === "result") {
        view.result = step.game;
        view.waiting = null;
        render();
        return setTimeout(next, ui.fast ? 1500 : 3500);
      }
      var a = step.action;
      var mine = a.team === "user";
      view = view && view.draft === step.draft ? view : { draft: step.draft, fearless: step.fearless, result: null };
      view.waiting = { team: a.team, type: a.type };
      view.last = null;
      render();
      setTimeout(function () {
        step.draft.actions.push(a);
        view.waiting = null;
        view.last = a;
        render();
        setTimeout(next, mine ? 700 : 400);
      }, mine ? 150 : delay());
    }
  }

  // Le mode automatique : à ton tour, il joue après le même délai.
  function scheduleAuto() {
    clearTimeout(auto.timer);
    auto.timer = null;
    if (!auto.on || queue.length || ui.busy || state.status !== "draft" || !state.draft) return;
    var turn = state.draft.turn;
    if (!turn || turn.team !== "user") return;
    auto.timer = setTimeout(function () {
      auto.timer = null;
      if (auto.on && !queue.length && !ui.busy) send({ type: turn.type, auto: true });
    }, delay());
  }

  function render() {
    var scroll = root.querySelector(".champ-grid");
    var top = scroll ? scroll.scrollTop : 0;
    var parts = [header()];
    if (ui.error) parts.push(h("p", { class: "flash flash-error", role: "alert", text: ui.error }));
    parts.push(result());
    parts.push(metaPanel());
    if (state.status === "lineup") parts.push(lineup());
    if (view || (state.status === "draft" && state.draft)) parts.push(draft());
    parts.push(games());
    parts.push(forfeitButton());
    root.replaceChildren.apply(root, parts.filter(Boolean));
    var grid = root.querySelector(".champ-grid");
    if (grid && !ui.flash) grid.scrollTop = top;
    if (ui.flash) {
      var played = document.getElementById("partie-" + ui.flash);
      ui.flash = null;
      if (played) played.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (!view) scheduleAuto();
  }

  function renderKeepFocus(name) {
    var input = root.querySelector("[data-focus=" + name + "]");
    var pos = input ? input.selectionStart : null;
    render();
    var again = root.querySelector("[data-focus=" + name + "]");
    if (again) { again.focus(); if (pos !== null) again.setSelectionRange(pos, pos); }
  }

  // 1V1 : l'état est redemandé toutes les 2 s (les coups d'en face, les délais).
  function signature(st) {
    var d = st.draft;
    return [st.raw_status, st.games.length, d ? d.actions.length : -1, st.lineup_done, st.opponent_ready,
      st.auto_forced, st.opponent_auto, st.deadline].join("|");
  }
  function poll() {
    if (ui.busy || queue.length || document.hidden) return;
    if (state.raw_status !== "lineup" && state.raw_status !== "draft") return;
    var needStatic = state.lineup_done && (!fixed.ratings || (state.opponent_ready && !fixed.hints));
    fetch(root.dataset.state + (needStatic ? "?static=1" : ""), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (ui.busy || queue.length || !data.state) return;
        if (data.static) { fixed = data.static; index(); }
        var before = state, after = data.state;
        if (signature(before) === signature(after) && !data.static) return;
        var played = after.games.length > before.games.length ? after.games[0].number : null;
        if (before.status === "draft" && before.draft && (after.draft || played)) {
          try { replay(before, after, played); } catch (e) { queue = []; view = null; state = after; render(); }
        } else { state = after; render(); }
      })
      .catch(function () { /* réessai au prochain tour */ });
  }
  if (DUEL) setInterval(poll, 2000);

  render();
})();

// La grille de champions de la draft : recherche, filtre, notes du joueur
// choisi (pick), et bouton de confirmation.
(function () {
  const form = document.querySelector("[data-draft]");
  if (!form) return;
  const grid = form.querySelector(".champ-grid");
  const tiles = Array.from(grid.querySelectorAll(".champ-tile"));
  const search = form.querySelector("[data-search]");
  const onlyPlayed = form.querySelector("[data-played]");
  const chosen = form.querySelector("[data-chosen]");
  const submit = form.querySelector("[data-submit]");
  const isPick = form.dataset.kind === "pick";

  function role() {
    const r = form.querySelector("input[name=role]:checked");
    return r ? r.value : null;
  }

  // Les notes du joueur choisi : "Top:72:12:1 Mid:41:0:0" → {rating, games, played}.
  function info(tile) {
    const wanted = role();
    for (const part of (tile.dataset.ratings || "").split(" ")) {
      const [r, rating, games, played] = part.split(":");
      if (r === wanted) return { rating: +rating, games: +games, played: played === "1" };
    }
    return { rating: 0, games: 0, played: false };
  }

  function refresh() {
    const q = search.value.trim().toLowerCase();
    if (isPick) {
      tiles.forEach((t) => {
        const i = info(t);
        t.classList.toggle("is-played", i.played);
        t.querySelector(".ct-note").textContent = i.rating;
        t.title = i.played ? `${i.games} partie${i.games > 1 ? "s" : ""}` : "jamais joué";
        t._sort = [i.played ? 0 : 1, -i.rating];
      });
      tiles.sort((a, b) => a._sort[0] - b._sort[0] || a._sort[1] - b._sort[1] || a.dataset.name.localeCompare(b.dataset.name))
        .forEach((t) => grid.appendChild(t));
    }
    tiles.forEach((t) => {
      const hidden = (q && !t.dataset.name.includes(q)) || (onlyPlayed.checked && !q && !t.classList.contains("is-played"));
      t.hidden = hidden;
    });
  }

  function select() {
    const input = form.querySelector("input[name=champion]:checked");
    tiles.forEach((t) => t.classList.toggle("is-selected", t.contains(input)));
    if (!input) return;
    const name = input.closest(".champ-tile").querySelector(".ct-name").textContent;
    const who = role();
    chosen.textContent = isPick ? `${name} pour ton ${who}` : `Bannir ${name}`;
    submit.disabled = false;
  }

  search.addEventListener("input", refresh);
  onlyPlayed.addEventListener("change", refresh);
  form.querySelectorAll("input[name=role]").forEach((r) => r.addEventListener("change", () => { refresh(); select(); }));
  grid.addEventListener("change", select);
  refresh();
})();

// Vitrine des boosters sans visuel : les joueurs du booster défilent, en
// légendaire, un toutes les quelques secondes (décalé d'un booster à l'autre).
(function () {
  const STEP = 2800;
  document.querySelectorAll("[data-showcase]").forEach((box, index) => {
    const cards = Array.from(box.querySelectorAll(".showcase-card"));
    if (cards.length < 2) return;
    let current = 0;
    const next = () => {
      cards[current].classList.remove("is-shown");
      current = (current + 1) % cards.length;
      cards[current].classList.add("is-shown");
    };
    setTimeout(() => {
      next();
      setInterval(() => { if (!document.hidden) next(); }, STEP);
    }, STEP + index * 450);
  });
})();

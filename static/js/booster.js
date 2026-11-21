// Ouverture d'un booster : le paquet tremble et se dechire, les cartes
// sont distribuees face cachee, puis retournees une a une (ou toutes).
// Les cartes rares ont un flash et des etincelles a leur couleur.
(function () {
  "use strict";

  var root = document.getElementById("opening");
  if (!root) return;

  var pack = document.getElementById("pack");
  var deal = document.getElementById("deal");
  var hint = document.getElementById("hint");
  var flipHint = document.getElementById("flip-hint");
  var summary = document.getElementById("summary");
  var flash = document.getElementById("flash");
  var revealAll = document.getElementById("reveal-all");
  var cards = Array.prototype.slice.call(document.querySelectorAll(".flip"));
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var opened = false;

  function wait(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, reduced ? 0 : ms);
    });
  }

  function flashOnce(color) {
    if (reduced || !flash) return;
    flash.style.setProperty("--flash", color || "#ffffff");
    flash.classList.remove("on");
    void flash.offsetWidth; // relance l'animation
    flash.classList.add("on");
  }

  function sparks(card, color, count) {
    if (reduced) return;
    for (var i = 0; i < count; i++) {
      var s = document.createElement("span");
      var angle = (Math.PI * 2 * i) / count;
      var dist = 90 + Math.random() * 70;
      s.className = "spark";
      s.style.setProperty("--dx", Math.cos(angle) * dist + "px");
      s.style.setProperty("--dy", Math.sin(angle) * dist + "px");
      s.style.background = color;
      card.appendChild(s);
      setTimeout(s.remove.bind(s), 1000);
    }
  }

  function allRevealed() {
    return cards.every(function (c) {
      return c.classList.contains("revealed");
    });
  }

  function showSummary() {
    if (flipHint) flipHint.hidden = true;
    summary.classList.add("shown");
  }

  function reveal(card) {
    if (card.classList.contains("revealed")) return;
    card.classList.add("revealed");
    var rarity = parseInt(card.getAttribute("data-rarity"), 10) || 0;
    var color = getComputedStyle(card).getPropertyValue("--rarity-color").trim();
    if (rarity >= 3) {
      flashOnce(color);
      sparks(card, color, 18);
    } else if (rarity === 2) {
      sparks(card, color, 10);
    }
    if (allRevealed()) setTimeout(showSummary, reduced ? 0 : 700);
  }

  // La rareté max du paquet se devine à l'ouverture : la lumière qui
  // s'échappe en tremblant prend sa couleur, plus fort pour une épique,
  // avec des rayons et une secousse de l'écran pour une légendaire.
  function openPack() {
    if (opened) return;
    opened = true;
    if (hint) hint.textContent = "";
    var best = parseInt(pack.getAttribute("data-best"), 10) || 0;
    var bestColor = getComputedStyle(pack).getPropertyValue("--best").trim() || "#ffffff";
    pack.classList.add("shaking", "charge-" + Math.min(best, 3));
    wait(best >= 3 ? 1100 : 750)
      .then(function () {
        pack.classList.remove("shaking");
        pack.classList.add("torn");
        flashOnce(best >= 1 ? bestColor : "#ffffff");
        if (best >= 3) {
          document.body.classList.add("quake");
          setTimeout(function () { document.body.classList.remove("quake"); }, 600);
        }
        return wait(best >= 2 ? 1000 : 800);
      })
      .then(function () {
        pack.hidden = true;
        if (hint) hint.hidden = true;
        deal.hidden = false;
        void deal.offsetWidth;
        deal.classList.add("dealt");
        if (deal.scrollIntoView && window.innerWidth < 700) {
          deal.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
        }
        return wait(cards.length * 140 + 550);
      })
      .then(function () {
        if (flipHint) flipHint.hidden = false;
      });
  }

  if (pack) {
    pack.addEventListener("click", openPack);
  } else {
    showSummary();
  }

  cards.forEach(function (card) {
    card.addEventListener("click", function (event) {
      if (!card.classList.contains("revealed")) {
        // 1er clic : retourne ; 2e clic : suit le lien vers la carte.
        event.preventDefault();
        reveal(card);
      }
    });
    card.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        if (!card.classList.contains("revealed")) {
          event.preventDefault();
          reveal(card);
        }
      }
    });
  });

  if (revealAll) {
    revealAll.addEventListener("click", function () {
      // La plus rare en dernier, pour garder le suspense.
      var hidden = cards
        .filter(function (c) {
          return !c.classList.contains("revealed");
        })
        .sort(function (a, b) {
          return (
            (parseInt(a.getAttribute("data-rarity"), 10) || 0) -
            (parseInt(b.getAttribute("data-rarity"), 10) || 0)
          );
        });
      hidden.forEach(function (c, i) {
        setTimeout(function () {
          reveal(c);
        }, reduced ? 0 : i * 350);
      });
    });
  }
})();

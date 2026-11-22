// Les comptes à rebours « prochain booster dans … » : chaque <time
// data-countdown datetime="…"> se met à jour chaque seconde et recharge
// la page quand un booster arrive.
(function () {
  "use strict";
  var nodes = Array.prototype.slice.call(document.querySelectorAll("time[data-countdown]"));
  if (!nodes.length) return;

  function format(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    var d = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    if (d) return d + " j " + h + " h";
    if (h) return h + " h " + String(m).padStart(2, "0");
    if (m) return m + " min " + String(sec).padStart(2, "0");
    return sec + " s";
  }

  var reloading = false;
  function tick() {
    var now = Date.now();
    nodes.forEach(function (node) {
      var left = new Date(node.getAttribute("datetime")).getTime() - now;
      node.textContent = format(left);
      if (left <= 0 && !reloading && node.hasAttribute("data-reload")) {
        reloading = true;
        setTimeout(function () { window.location.reload(); }, 800);
      }
    });
  }
  tick();
  setInterval(tick, 1000);
})();

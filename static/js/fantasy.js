// La Fantasy : le choix de l'équipe. Remet les choix enregistrés, affiche la
// photo, le coût total face au plafond et le bonus de diversité, et empêche
// d'aligner deux fois le même joueur.
(function () {
  "use strict";
  var form = document.getElementById("fy-form");
  if (!form) return;
  var players = JSON.parse(document.getElementById("fy-players").textContent);
  var picked = JSON.parse(document.getElementById("fy-picked").textContent) || {};
  var selects = Array.prototype.slice.call(form.querySelectorAll("select[data-pick]"));
  var capText = document.getElementById("fy-budget").textContent.match(/\/\s*([\d.,]+)/);
  var cap = capText ? parseFloat(capText[1].replace(",", ".")) : 0;

  selects.forEach(function (sel) {
    var value = picked[sel.name];
    if (value && players[value]) sel.value = String(value);
  });

  function update() {
    var used = {}, cost = 0, teams = {};
    selects.forEach(function (sel) {
      var p = players[sel.value];
      var slot = sel.closest(".fy-slot");
      var img = slot.querySelector("[data-photo]");
      if (p) {
        cost += p.cost;
        used[sel.value] = (used[sel.value] || 0) + 1;
        if (p.team) teams[p.team] = true;
        if (p.photo) { img.src = p.photo; img.style.visibility = "visible"; } else { img.style.visibility = "hidden"; }
      } else {
        img.style.visibility = "hidden";
      }
    });
    selects.forEach(function (sel) {
      sel.closest(".fy-slot").classList.toggle("is-dup", !!sel.value && used[sel.value] > 1);
      Array.prototype.forEach.call(sel.options, function (opt) {
        opt.disabled = !!opt.value && opt.value !== sel.value && !!used[opt.value];
      });
    });
    var nTeams = Object.keys(teams).length;
    var div = Math.min(20, 5 * Math.max(0, nTeams - 1));
    document.getElementById("fy-cost").textContent = cost.toFixed(1).replace(".", ",");
    document.getElementById("fy-div").textContent = "+" + div + " %";
    var over = cap && cost > cap + 1e-6;
    document.getElementById("fy-budget").classList.toggle("is-over", !!over);
    document.getElementById("fy-meter").style.width = (cap ? Math.min(100, cost / cap * 100) : 0) + "%";
    document.getElementById("fy-save").disabled = !!over;
  }
  selects.forEach(function (sel) { sel.addEventListener("change", update); });
  update();
})();

"""Un état des lieux de la qualité des données importées.

python manage.py diagnostic_donnees [--brut "LPL/2024 Season/Summer Season"] [--sortie rapport.txt]

1. Dans la base : pour chaque ligue et année, la part des lignes de
   scoreboard sans dégâts, sans or, sans CS, sans durée, sans vision, sans
   kills d'équipe ; et les joueurs dont une stat de split vaut zéro.
2. --brut : ce que Leaguepedia renvoie vraiment pour un tournoi (champs
   vides ou absents), pour comparer avec ce qui a été enregistré.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from esport.models import GameAppearance, PlayerSplitStats, Split

CHECKS = {
    "degats": Q(damage=0) | Q(damage__isnull=True),
    "or": Q(gold=0) | Q(gold__isnull=True),
    "cs": Q(cs=0) | Q(cs__isnull=True),
    "duree": Q(game_length__isnull=True) | Q(game_length=0),
    "vision": Q(vision_score__isnull=True),
    "kills_eq": Q(team_kills=0) | Q(team_kills__isnull=True),
    "degats_eq": Q(team_damage=0) | Q(team_damage__isnull=True),
    "role": Q(role=""),
    "equipe": Q(team__isnull=True),
}


class Command(BaseCommand):
    help = "Rapport sur les données manquantes ou suspectes (base, et Leaguepedia brut avec --brut)."

    def add_arguments(self, parser):
        parser.add_argument("--brut", action="append", default=[], help="OverviewPage Leaguepedia à examiner.")
        parser.add_argument("--sortie", default="", help="Écrire aussi le rapport dans ce fichier.")

    def handle(self, *args, **opts):
        lines: list[str] = []

        def out(text=""):
            lines.append(text)
            self.stdout.write(text)

        out("== Lignes de scoreboard sans donnée, par ligue et année ==")
        groups = (
            GameAppearance.objects.values("tournament__league__short_name", "tournament__split__year")
            .annotate(total=Count("id"), **{k: Count("id", filter=q) for k, q in CHECKS.items()})
            .order_by("tournament__league__short_name", "tournament__split__year")
        )
        out("ligue  année  lignes  " + "  ".join(CHECKS))
        for g in groups:
            total = g["total"] or 1
            cells = [f"{100 * g[k] / total:6.1f}%" for k in CHECKS]
            out(f"{g['tournament__league__short_name'] or '?':6} {g['tournament__split__year'] or '?':5} "
                f"{g['total']:7}  " + "  ".join(cells))

        out("")
        out("== Stats de split (joueurs avec au moins 3 parties) : zéros suspects et données absentes ==")
        stats = PlayerSplitStats.objects.filter(games__gte=3).select_related("player", "split")
        zero, absent = Counter(), Counter()
        examples = defaultdict(list)
        fields = ("damage_per_min", "cs_per_min", "gold_per_min", "vision_per_min", "kill_participation",
                  "damage_share", "gold_share")
        for s in stats:
            for field in fields:
                value = getattr(s, field, None)
                if value is None:
                    absent[field] += 1
                elif value == 0:
                    zero[field] += 1
                    if len(examples[field]) < 8:
                        examples[field].append(f"{s.player.ign} ({s.split.name}, {s.games} parties)")
        out(f"{stats.count()} stats de split examinées")
        for field in fields:
            out(f"  {field:18} zéro : {zero[field]:5}   absente (Leaguepedia) : {absent[field]:5}"
                + (f"   ex. {', '.join(examples[field])}" if examples[field] else ""))

        out("")
        out("== Les mieux notés de chaque split ==")
        for split in Split.objects.filter(player_stats__isnull=False).distinct().order_by("-year", "name"):
            top = split.player_stats.select_related("player").order_by("-score")[:5]
            out(f"  {split.name} : " + ", ".join(
                f"{t.player.ign} {t.score:.0f} (DPM {t.damage_per_min if t.damage_per_min is not None else '—'})"
                for t in top))

        for page in opts["brut"]:
            out("")
            out(f"== Leaguepedia brut : {page} ==")
            from esport.importer import LeaguepediaImporter

            rows = LeaguepediaImporter().fetch_scoreboard(type("T", (), {"overview_page": page})())
            out(f"{len(rows)} lignes")
            empty = Counter()
            for r in rows:
                for key, value in r.items():
                    if value in (None, "", "0"):
                        empty[key] += 1
            for key, n in empty.most_common():
                out(f"  {key} vide ou 0 : {n} / {len(rows)}")
            for r in rows[:3]:
                out("  exemple : " + json.dumps(r, ensure_ascii=False))
            games = LeaguepediaImporter().client.cargo(
                tables="ScoreboardGames=SG",
                fields="SG.GameId=GameId,SG.Gamelength=Gamelength,SG.Gamelength_Number=GamelengthNumber,"
                       "SG.Team1=Team1,SG.Team2=Team2,SG.Team1Kills=Team1Kills,SG.Team1Gold=Team1Gold",
                where=f"SG.OverviewPage='{page}'",
            )
            missing = sum(1 for g in games if not g.get("GamelengthNumber"))
            out(f"  ScoreboardGames : {len(games)} parties, {missing} sans Gamelength_Number")
            for g in games[:2]:
                out("  partie : " + json.dumps(g, ensure_ascii=False))

        if opts["sortie"]:
            with open(opts["sortie"], "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

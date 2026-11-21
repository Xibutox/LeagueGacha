from decimal import Decimal

from django.db import migrations

OLD = {"commune": 62, "rare": 27, "epique": 9, "legendaire": 2}
NEW = {"commune": 74, "rare": 20, "epique": 5, "legendaire": 1}


def new_rates(apps, schema_editor):
    """Nouveaux taux par défaut (74 / 20 / 5 / 1 %). Appliqués seulement si
    les quatre raretés ont encore les anciens taux par défaut : des taux
    choisis par l'admin ne sont pas écrasés."""
    Rarity = apps.get_model("cards", "Rarity")
    rarities = {r.code: r for r in Rarity.objects.filter(code__in=OLD)}
    if len(rarities) != len(OLD):
        return
    if any(Decimal(rarities[c].drop_rate) != Decimal(v) for c, v in OLD.items()):
        return
    for code, rate in NEW.items():
        rarities[code].drop_rate = rate
        rarities[code].save(update_fields=["drop_rate"])


class Migration(migrations.Migration):
    dependencies = [("cards", "0009_auto_boosters_guaranteed_rare")]
    operations = [migrations.RunPython(new_rates, migrations.RunPython.noop)]

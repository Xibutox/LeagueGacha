from django.db import migrations


def guarantee_rare(apps, schema_editor):
    """Les boosters créés automatiquement sans rareté garantie reçoivent
    « rare » sur la dernière carte (une seule fois : un réglage choisi
    ensuite par l'admin n'est plus touché)."""
    Booster = apps.get_model("cards", "Booster")
    Rarity = apps.get_model("cards", "Rarity")
    rare = Rarity.objects.filter(code="rare").first()
    if rare is None:
        return
    for booster in Booster.objects.filter(auto=True, guaranteed_rarity__isnull=True):
        booster.guaranteed_rarity = rare
        if "Dernière carte au moins rare" not in booster.description:
            booster.description = (booster.description + " Dernière carte au moins rare.").strip()
        booster.save(update_fields=["guaranteed_rarity", "description"])


class Migration(migrations.Migration):
    dependencies = [("cards", "0008_booster_gifts")]
    operations = [migrations.RunPython(guarantee_rare, migrations.RunPython.noop)]

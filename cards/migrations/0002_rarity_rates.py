"""Les poids de tirage deviennent des taux en pourcentage.

Les poids existants sont convertis : chaque poids devient sa part du total
(62 / 27 / 9 / 2 sur 100 donnent les mêmes taux). Les poids propres à un
booster sont convertis de la même façon, sur le total des poids par
défaut.
"""

from decimal import ROUND_HALF_UP, Decimal

import django.core.validators
from django.db import migrations, models


def to_rate(weight, total):
    if not total:
        return Decimal("0")
    return (Decimal(100) * weight / total).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def weights_to_rates(apps, schema_editor):
    Rarity = apps.get_model("cards", "Rarity")
    BoosterRarityWeight = apps.get_model("cards", "BoosterRarityWeight")
    total = sum(Rarity.objects.values_list("drop_weight", flat=True))

    for rarity in Rarity.objects.all():
        rarity.drop_rate = to_rate(rarity.drop_weight, total)
        rarity.save(update_fields=["drop_rate"])

    for weight in BoosterRarityWeight.objects.all():
        weight.rate = min(Decimal(100), to_rate(weight.weight, total))
        weight.save(update_fields=["rate"])


def rates_to_weights(apps, schema_editor):
    Rarity = apps.get_model("cards", "Rarity")
    BoosterRarityWeight = apps.get_model("cards", "BoosterRarityWeight")

    for rarity in Rarity.objects.all():
        rarity.drop_weight = int(round(rarity.drop_rate))
        rarity.save(update_fields=["drop_weight"])

    for weight in BoosterRarityWeight.objects.all():
        weight.weight = int(round(weight.rate))
        weight.save(update_fields=["weight"])


RATE_VALIDATORS = [
    django.core.validators.MinValueValidator(Decimal("0")),
    django.core.validators.MaxValueValidator(Decimal("100")),
]


class Migration(migrations.Migration):
    dependencies = [("cards", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="rarity",
            name="drop_rate",
            field=models.DecimalField(
                "taux de tirage (%)",
                max_digits=5,
                decimal_places=2,
                default=Decimal("0"),
                validators=RATE_VALIDATORS,
                help_text="Chance qu'un emplacement de booster soit de cette rareté.",
            ),
        ),
        migrations.AddField(
            model_name="boosterrarityweight",
            name="rate",
            field=models.DecimalField(
                "taux de tirage (%)",
                max_digits=5,
                decimal_places=2,
                default=Decimal("0"),
                validators=RATE_VALIDATORS,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(weights_to_rates, rates_to_weights),
        migrations.RemoveField(model_name="rarity", name="drop_weight"),
        migrations.RemoveField(model_name="boosterrarityweight", name="weight"),
        migrations.AlterModelOptions(
            name="boosterrarityweight",
            options={
                "verbose_name": "taux de rareté du booster",
                "verbose_name_plural": "taux de rareté du booster",
            },
        ),
    ]

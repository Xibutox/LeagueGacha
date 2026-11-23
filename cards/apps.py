from django.apps import AppConfig


class CardsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cards"
    verbose_name = "Jeu de cartes"

    def ready(self):
        from . import catalog

        catalog.connect_signals()
        from . import tasks  # noqa: F401 - les tâches planifiées, déclarées au démarrage

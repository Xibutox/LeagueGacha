from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Ancien nom de init_boosters : raretés par défaut et boosters types."

    def handle(self, *args, **opts):
        call_command("init_boosters", stdout=self.stdout)

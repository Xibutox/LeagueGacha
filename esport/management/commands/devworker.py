"""Le worker Celery du développement, relancé à chaque changement de code.

Celery ne se recharge pas tout seul : sans ceci, une modification de
esport/ ou cards/ n'est prise en compte par les imports lancés depuis la
gestion qu'après un redémarrage manuel du conteneur ``worker``.
"""

import signal
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

WATCHED = ("esport", "cards", "config")


def snapshot(base: Path) -> dict[str, float]:
    """L'heure de modification de chaque fichier Python surveillé."""
    files = {}
    for folder in WATCHED:
        for path in (base / folder).rglob("*.py"):
            try:
                files[str(path)] = path.stat().st_mtime
            except OSError:
                continue
    return files


class Command(BaseCommand):
    help = "Lance le worker Celery et le relance quand le code du projet change."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=2.0)

    def handle(self, *args, interval=2.0, **opts):
        base = Path(settings.BASE_DIR)
        command = [sys.executable, "-m", "celery", "-A", "config.celery", "worker", "--loglevel=info"]
        worker = None
        seen = None
        stopping = False

        def stop(*_):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        try:
            while not stopping:
                current = snapshot(base)
                if current != seen or (worker and worker.poll() is not None):
                    if worker is not None:
                        self.stdout.write("Code modifié : redémarrage du worker…")
                        self._stop(worker)
                    worker = subprocess.Popen(command)
                    seen = current
                time.sleep(interval)
        finally:
            if worker is not None:
                self._stop(worker)

    @staticmethod
    def _stop(worker):
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=30)
            except subprocess.TimeoutExpired:
                worker.kill()

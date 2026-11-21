@echo off
rem Lance l'environnement de dev : sous-module django-generic + stack Docker.
rem Double-clic pour lancer ; relancable sans risque.
chcp 65001 >nul
cd /d "%~dp0"

where git >nul 2>nul || (echo [ERREUR] Git est introuvable. & pause & exit /b 1)
where docker >nul 2>nul || (echo [ERREUR] Docker est introuvable. & pause & exit /b 1)

if not exist ".git" (
  echo == Creation du depot git
  git init || (pause & exit /b 1)
)

if not exist "django-generic\pyproject.toml" (
  echo == Ajout du sous-module django-generic
  git submodule add https://github.com/Xibutox/django-generic.git django-generic || git submodule update --init --recursive || (pause & exit /b 1)
)

docker info >nul 2>nul || (echo [ERREUR] Docker Desktop n'est pas demarre : lance-le puis relance ce script. & pause & exit /b 1)

echo == Construction et demarrage de la stack (la premiere fois : quelques minutes)
docker compose up --build -d || (pause & exit /b 1)

echo.
echo == Compte administrateur (Ctrl+C pour passer s'il existe deja)
docker compose exec web python manage.py createsuperuser

echo.
echo Jeu      : http://localhost:8000/
echo Gestion  : http://localhost:8000/gestion/
start "" http://localhost:8000/gestion/
pause

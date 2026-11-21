# Copie les données du PC (base et images) sur le Raspberry Pi.
# Lancé par exporter-donnees.bat. La stack de dev doit tourner sur ce PC.
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root
$export = Join-Path $root "deploy\export"
New-Item -ItemType Directory -Force $export | Out-Null

Write-Host "== Export de la base du PC" -ForegroundColor Cyan
# cmd pour garder le fichier binaire intact (PowerShell 5 le réencoderait).
cmd /c "docker compose exec -T db pg_dump -U wikilol -Fc wikilol > ""$export\base.dump"""
if ($LASTEXITCODE -ne 0) { throw "pg_dump a échoué : la stack de dev tourne-t-elle (demarrer.bat) ?" }

Write-Host "== Export des images (photos, logos, champions, visuels)" -ForegroundColor Cyan
$folders = @("game_media", "media") | Where-Object { Test-Path (Join-Path $root $_) }
tar -czf "$export\fichiers.tar.gz" @folders
if ($LASTEXITCODE -ne 0) { throw "tar a échoué." }

$address = Read-Host "Adresse du Raspberry (ex. 192.168.1.42)"
$user = Read-Host "Utilisateur du Pi [pi]"
if (-not $user) { $user = "pi" }
$target = "$user@$address"

Write-Host "== Copie sur le Pi" -ForegroundColor Cyan
scp "$export\base.dump" "$export\fichiers.tar.gz" "${target}:"
if ($LASTEXITCODE -ne 0) { throw "Copie impossible." }

Write-Host "== Import sur le Pi (une sauvegarde de ses données actuelles est faite avant)" -ForegroundColor Cyan
ssh -t $target "cd /opt/wiki-lol-esport && ./deploy/raspberry/importer-donnees.sh ~/base.dump ~/fichiers.tar.gz && rm ~/base.dump ~/fichiers.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "L'import s'est arrêté : relis le message ci-dessus." }
Write-Host "Données copiées sur le Pi." -ForegroundColor Green

# Installe le Raspberry Pi depuis ce PC : ajoute ta clé SSH sur le Pi
# (le mot de passe du Pi est demandé une dernière fois), y copie
# install.sh et le lance. Lancé par installer-depuis-windows.bat.
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")

foreach ($cmd in "ssh", "scp", "ssh-keygen") {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "Client OpenSSH introuvable : Paramètres > Système > Fonctionnalités facultatives > Client OpenSSH."
    }
}

Write-Host ""
Write-Host "== Installation du Raspberry Pi" -ForegroundColor Cyan
Write-Host "L'adresse IP du Pi se lit dans Freebox OS > Périphériques réseau."
$address = Read-Host "Adresse du Raspberry (ex. 192.168.1.42)"
if (-not $address) { throw "Adresse manquante." }
$user = Read-Host "Utilisateur du Pi [pi]"
if (-not $user) { $user = "pi" }
$target = "$user@$address"

# 1. Une clé SSH sur ce PC (créée une seule fois).
$key = Join-Path $env:USERPROFILE ".ssh\id_ed25519"
if (-not (Test-Path $key)) {
    Write-Host ""
    Write-Host "== Création de ta clé SSH (Entrée deux fois = pas de phrase de passe)" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force (Split-Path $key) | Out-Null
    ssh-keygen -t ed25519 -f $key -C "$env:USERNAME@$env:COMPUTERNAME"
    if ($LASTEXITCODE -ne 0) { throw "ssh-keygen a échoué." }
}
$pub = (Get-Content "$key.pub" -Raw).Trim()

# 2. La clé sur le Pi : le mot de passe est demandé ici, tapé par toi.
Write-Host ""
Write-Host "== Ajout de ta clé sur le Pi : tape le mot de passe du Pi quand il est demandé" -ForegroundColor Cyan
ssh -o StrictHostKeyChecking=accept-new $target "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; grep -qxF '$pub' ~/.ssh/authorized_keys || echo '$pub' >> ~/.ssh/authorized_keys"
if ($LASTEXITCODE -ne 0) { throw "Connexion au Pi impossible : vérifie l'adresse, que le Pi est allumé et que SSH y est activé." }

# 3. install.sh (fins de ligne Unix) sur le Pi, puis on le lance.
$script = Join-Path $root "deploy\raspberry\install.sh"
$tmp = Join-Path $env:TEMP "wiki-lol-install.sh"
$text = [IO.File]::ReadAllText($script) -replace "`r`n", "`n"
[IO.File]::WriteAllText($tmp, $text, (New-Object Text.UTF8Encoding $false))
scp $tmp "${target}:install.sh"
if ($LASTEXITCODE -ne 0) { throw "Copie de install.sh impossible." }

Write-Host ""
Write-Host "== Lancement de l'installation sur le Pi" -ForegroundColor Cyan
ssh -t $target "bash ~/install.sh"
if ($LASTEXITCODE -ne 0) { throw "L'installation s'est arrêtée : relis le message ci-dessus, puis relance ce script (il reprend où il en était)." }

Write-Host ""
Write-Host "Terminé. Pour te reconnecter au Pi : ssh $target" -ForegroundColor Green

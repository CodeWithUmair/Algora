# Run this ON THE VPS (as Administrator) to prepare a fresh Windows Server
# box to run this bot. Installs Chocolatey, Python, Git, clones the repo,
# checks out GOLD, and installs the Python deps. It does NOT install or log
# into the MT5 terminal - that's a manual GUI step (see README note below).

$ErrorActionPreference = "Stop"

if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
}

choco install -y python312 git
refreshenv

$repoDir = "$env:USERPROFILE\Desktop\Algora"
if (-not (Test-Path $repoDir)) {
    git clone https://github.com/CodeWithUmair/Algora.git $repoDir
}
Set-Location $repoDir
git fetch origin
git checkout GOLD
git pull origin GOLD

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Write-Host ""
Write-Host "Repo ready at $repoDir on branch GOLD." -ForegroundColor Green
Write-Host "Still manual: install the MT5 terminal (download from your broker) and log in to the account once through its GUI so the credentials are saved - mt5_bridge.py attaches to that already-logged-in terminal, it does not log in itself." -ForegroundColor Yellow
Write-Host "Then run: python trading_bot\run_live_auto_bot.py" -ForegroundColor Yellow

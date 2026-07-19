# run.ps1 - launch the full Orchestraty system (all services) and open the Control Tower.
# Usage:  ./run.ps1            start everything + open dashboard
#         ./run.ps1 -Fresh     wipe volumes first (clean slate)
#         ./run.ps1 -NoBrowser don't open browser tabs
param([switch]$Fresh, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "==== ORCHESTRATY ====" -ForegroundColor Green

# 1. Docker running?
try { docker info *> $null } catch {
    Write-Host "Docker isn't running. Start Docker Desktop and re-run ./run.ps1" -ForegroundColor Red
    exit 1
}

# 2. Bring up the stack
if ($Fresh) { Write-Host "Wiping volumes..." -ForegroundColor Yellow; docker compose down -v }
Write-Host "Building + starting all services (first run pulls images - can take a few minutes)..." -ForegroundColor Cyan
docker compose up --build -d
if ($LASTEXITCODE -ne 0) { Write-Host "docker compose failed. See output above." -ForegroundColor Red; exit 1 }

# 3. Wait for the orchestrator to be healthy
Write-Host "Waiting for the orchestrator to come up..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $h = Invoke-RestMethod "http://localhost:8000/health" -TimeoutSec 3
        if ($h.status -eq "healthy") { $ok = $true; break }
    } catch {}
    Start-Sleep -Seconds 2
}
if ($ok) { Write-Host "Orchestrator is healthy." -ForegroundColor Green }
else { Write-Host "Orchestrator not healthy yet - check: docker compose logs orchestrator" -ForegroundColor Yellow }

# 4. Print URLs
Write-Host ""
Write-Host "  Control Tower : http://localhost:8000/ui/" -ForegroundColor Green
Write-Host "  API docs      : http://localhost:8000/docs" -ForegroundColor Green
Write-Host "  Grafana       : http://localhost:3000" -ForegroundColor Green
Write-Host "  Prometheus    : http://localhost:9090" -ForegroundColor Green
Write-Host "  MLflow        : http://localhost:5000" -ForegroundColor Green
Write-Host "  Model server  : http://localhost:8080/docs" -ForegroundColor Green
Write-Host ""
Write-Host "  Stop: docker compose down      Logs: docker compose logs -f" -ForegroundColor Gray
Write-Host ""

# 5. Open the dashboard + Grafana
if (-not $NoBrowser) {
    Start-Process "http://localhost:8000/ui/"
    Start-Process "http://localhost:3000"
}

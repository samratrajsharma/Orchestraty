# dev.ps1 - start the Orchestraty control plane locally (Postgres + Redis + orchestrator API).
# Usage:  ./dev.ps1         start
#         ./dev.ps1 -Fresh  wipe volumes and rebuild
param([switch]$Fresh)
$ErrorActionPreference = "Stop"

if ($Fresh) {
    Write-Host "Wiping volumes..." -ForegroundColor Yellow
    docker compose down -v
}

Write-Host "Starting Orchestraty control plane..." -ForegroundColor Cyan
docker compose up --build -d

Write-Host ""
Write-Host "  Orchestrator API : http://localhost:8000" -ForegroundColor Green
Write-Host "  Health           : http://localhost:8000/health" -ForegroundColor Green
Write-Host "  API docs         : http://localhost:8000/docs" -ForegroundColor Green
Write-Host ""
Write-Host "  Logs : docker compose logs -f orchestrator" -ForegroundColor Gray
Write-Host "  Stop : docker compose down" -F
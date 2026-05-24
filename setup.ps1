param(
    [switch]$Fresh
)

$ErrorActionPreference = "Continue"
$COMPOSE_FILE = "infrastructure/docker-compose.yml"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Orchestraty - Enterprise AI OS Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($Fresh) {
    Write-Host "[0/6] FRESH START - wiping all data..." -ForegroundColor Red
    Write-Host "  Stopping containers and removing volumes..." -ForegroundColor Gray
    docker compose -f $COMPOSE_FILE down -v
    Write-Host "  All data wiped (database, files, cache, vectors)" -ForegroundColor Red
    Write-Host ""
}

Write-Host "[1/6] Building and starting Docker services..." -ForegroundColor Yellow
docker compose -f $COMPOSE_FILE up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker Compose failed. Is Docker running?" -ForegroundColor Red
    exit 1
}
Write-Host "  Docker services starting..." -ForegroundColor Green
Write-Host ""

Write-Host "[2/6] Waiting for services to be ready..." -ForegroundColor Yellow

$maxRetries = 30
$retry = 0
while ($retry -lt $maxRetries) {
    $pgCheck = docker exec orchestraty-postgres pg_isready -U orchestraty -d orchestraty 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  PostgreSQL: Ready" -ForegroundColor Green
        break
    }
    $retry++
    Write-Host "  Waiting for PostgreSQL... ($retry/$maxRetries)" -ForegroundColor Gray
    Start-Sleep -Seconds 2
}
if ($retry -ge $maxRetries) {
    Write-Host "  WARNING: PostgreSQL may not be ready" -ForegroundColor Yellow
}

$retry = 0
while ($retry -lt 15) {
    $redisCheck = docker exec orchestraty-redis redis-cli ping 2>&1
    if ("$redisCheck" -match "PONG") {
        Write-Host "  Redis: Ready" -ForegroundColor Green
        break
    }
    $retry++
    Start-Sleep -Seconds 2
}

$retry = 0
while ($retry -lt 30) {
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($resp.status -eq "healthy") {
            Write-Host "  API Server: Ready" -ForegroundColor Green
            break
        }
    }
    catch {
        # API not ready yet
    }
    $retry++
    Write-Host "  Waiting for API server... ($retry/30)" -ForegroundColor Gray
    Start-Sleep -Seconds 3
}
if ($retry -ge 30) {
    Write-Host "  WARNING: API may not be ready yet. Check logs with:" -ForegroundColor Yellow
    Write-Host "  docker compose -f $COMPOSE_FILE logs api --tail 50" -ForegroundColor Gray
}

try {
    Invoke-WebRequest -Uri "http://localhost:9001" -UseBasicParsing -TimeoutSec 3 -ErrorAction SilentlyContinue | Out-Null
    Write-Host "  MinIO: Ready" -ForegroundColor Green
}
catch {
    Write-Host "  MinIO: Starting..." -ForegroundColor Gray
}

try {
    Invoke-RestMethod -Uri "http://localhost:6333/collections" -TimeoutSec 3 -ErrorAction SilentlyContinue | Out-Null
    Write-Host "  Qdrant: Ready" -ForegroundColor Green
}
catch {
    Write-Host "  Qdrant: Starting..." -ForegroundColor Gray
}

Write-Host ""

Write-Host "[3/6] Database tables auto-created on API startup" -ForegroundColor Yellow
Write-Host "  (init_db runs via FastAPI lifespan)" -ForegroundColor Green
Write-Host ""

Write-Host "[4/6] SuperAdmin auto-created on API startup" -ForegroundColor Yellow
$saEmail = "admin@orchestraty.com"
$envLines = Get-Content ".env" -ErrorAction SilentlyContinue
foreach ($line in $envLines) {
    if ($line -match "^SUPERADMIN_EMAIL=(.+)$") { $saEmail = $Matches[1] }
}
Write-Host "  SuperAdmin email: $saEmail" -ForegroundColor Magenta
Write-Host "  (password configured in .env SUPERADMIN_PASSWORD)" -ForegroundColor Gray
Write-Host ""

Write-Host "[5/6] Checking Celery worker..." -ForegroundColor Yellow
$celeryCheck = docker inspect --format "{{.State.Status}}" orchestraty-celery-worker 2>&1
if ("$celeryCheck" -match "running") {
    Write-Host "  Celery Worker: Running" -ForegroundColor Green
}
else {
    Write-Host "  Celery Worker: $celeryCheck (check logs if issues)" -ForegroundColor Yellow
}
Write-Host ""

Write-Host "[6/6] Setup complete! Launching Orchestraty..." -ForegroundColor Yellow

# Install frontend dependencies if needed
$frontendDir = "frontend/Frontend"
if (-not (Test-Path "$frontendDir/node_modules")) {
    Write-Host "  Installing frontend dependencies..." -ForegroundColor Yellow
    Push-Location $frontendDir
    npm install
    Pop-Location
}

# Launch SuperAdmin panel (:5174) in background
Write-Host "  Starting SuperAdmin panel on :5174..." -ForegroundColor Gray
$saJob = Start-Job -ScriptBlock {
    param($dir)
    Set-Location $dir
    npx vite --config vite.superadmin.config.ts --host 0.0.0.0
} -ArgumentList (Resolve-Path $frontendDir).Path

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Orchestraty is running!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Main App:         http://localhost:5173" -ForegroundColor Green
Write-Host "  SuperAdmin Panel: http://localhost:5174" -ForegroundColor Green
Write-Host "  API Docs:         http://localhost:8000/docs" -ForegroundColor Green
Write-Host "  MinIO:            http://localhost:9001" -ForegroundColor Green
Write-Host ""
Write-Host "  SuperAdmin:  $saEmail" -ForegroundColor Magenta
Write-Host ""
Write-Host "  Useful commands:" -ForegroundColor White
Write-Host "    Quick start:  .\start.ps1" -ForegroundColor Gray
Write-Host "    View logs:    docker compose -f $COMPOSE_FILE logs -f api" -ForegroundColor Gray
Write-Host "    Stop all:     docker compose -f $COMPOSE_FILE down" -ForegroundColor Gray
Write-Host "    Fresh reset:  .\setup.ps1 -Fresh" -ForegroundColor Gray
Write-Host ""
Write-Host "  Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

# Launch main React dev server (blocking — Ctrl+C to stop)
Push-Location $frontendDir
try {
    npx vite --host 0.0.0.0 --open
} finally {
    Stop-Job $saJob -ErrorAction SilentlyContinue
    Remove-Job $saJob -ErrorAction SilentlyContinue
    Pop-Location
}

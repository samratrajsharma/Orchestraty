param(
    [switch]$Fresh,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Continue"
$COMPOSE_FILE = "infrastructure/docker-compose.yml"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Orchestraty - Start" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── Fresh wipe if requested ──────────────────────────────────────────────
if ($Fresh) {
    Write-Host "[!] FRESH START - wiping all data..." -ForegroundColor Red
    docker compose -f $COMPOSE_FILE down -v 2>$null
    Write-Host "  Data wiped." -ForegroundColor Red
    Write-Host ""
}

# ── Step 1: Start Docker services ────────────────────────────────────────
Write-Host "[1/3] Starting Docker services..." -ForegroundColor Yellow

# Build only if -Fresh or first time
if ($Fresh) {
    docker compose -f $COMPOSE_FILE up -d --build
} else {
    docker compose -f $COMPOSE_FILE up -d
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker Compose failed. Is Docker running?" -ForegroundColor Red
    exit 1
}
Write-Host "  Docker services started." -ForegroundColor Green
Write-Host ""

# ── Step 2: Wait for API to be ready ────────────────────────────────────
Write-Host "[2/3] Waiting for API..." -ForegroundColor Yellow

$retry = 0
$maxRetries = 40
while ($retry -lt $maxRetries) {
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($resp.status -eq "healthy") {
            Write-Host "  API ready." -ForegroundColor Green
            break
        }
    }
    catch { }
    $retry++
    if ($retry % 5 -eq 0) {
        Write-Host "  Still waiting... ($retry/$maxRetries)" -ForegroundColor Gray
    }
    Start-Sleep -Seconds 2
}
if ($retry -ge $maxRetries) {
    Write-Host "  WARNING: API may not be ready. Check: docker compose -f $COMPOSE_FILE logs api" -ForegroundColor Yellow
}
Write-Host ""

# ── Step 3: Launch Streamlit ─────────────────────────────────────────────
Write-Host "[3/3] Launching Orchestraty..." -ForegroundColor Yellow
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Orchestraty is running!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  App:      http://localhost:8501" -ForegroundColor Green
Write-Host "  API Docs: http://localhost:8000/docs" -ForegroundColor Green
Write-Host "  MinIO:    http://localhost:9001" -ForegroundColor Green
Write-Host ""
Write-Host "  Admin:    admin@orchestraty.com / Admin@12345" -ForegroundColor Gray
Write-Host ""
Write-Host "  Press Ctrl+C to stop Streamlit." -ForegroundColor Gray
Write-Host ""

# Launch Streamlit (blocking — Ctrl+C to stop)
if ($NoBrowser) {
    streamlit run frontend/app.py --server.headless true
} else {
    streamlit run frontend/app.py
}

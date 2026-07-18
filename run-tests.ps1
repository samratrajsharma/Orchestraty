# run-tests.ps1 - run the Orchestraty unit test suite.
# Prereq:  pip install -r requirements-dev.txt
$ErrorActionPreference = "Continue"
$fail = 0
foreach ($c in "orchestrator", "agents", "model_server") {
    Write-Host ""
    Write-Host "== $c ==" -ForegroundColor Cyan
    Push-Location $c
    $env:PYTHONPATH = "."
    pytest tests/ -q
    if ($LASTEXITCODE -ne 0) { $fail = 1 }
    Pop-Location
}
Write-Host ""
if ($fail) { Write-Host "TESTS FAILED" -ForegroundColor Red; exit 1 }
Write-Host "ALL UNIT TESTS PASSED" -ForegroundColor Green

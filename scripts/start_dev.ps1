# Aegis — Closed-market dev startup (Windows)
# Usage: .\scripts\start_dev.ps1

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Aegis — Dev Mode Startup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This starts the TRADING ENGINE (port 8050)." -ForegroundColor Yellow
Write-Host "In a SECOND terminal, run:" -ForegroundColor Yellow
Write-Host "  cd frontend" -ForegroundColor White
Write-Host "  npm run dev" -ForegroundColor White
Write-Host "Then open: http://localhost:5173" -ForegroundColor Green
Write-Host ""
Write-Host "Playbook: docs/COFOUNDER_PLAYBOOK.md" -ForegroundColor Gray
Write-Host ""

python generate_token.py --validate
if ($LASTEXITCODE -ne 0) {
    Write-Host "Token invalid — starting auto-login..." -ForegroundColor Yellow
    python generate_token.py
}

python run.py --ensure-token --dev --sim-vol 1.5
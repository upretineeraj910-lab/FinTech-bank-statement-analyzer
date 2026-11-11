Write-Host "========================================================" -ForegroundColor Cyan
Write-Host " FinTech Bank Statement Analyzer - Launcher" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

if (Test-Path ".\venv\Scripts\python.exe") {
    Write-Host "[OK] Using virtual environment Python..." -ForegroundColor Green
    & ".\venv\Scripts\python.exe" server.py $args
} else {
    Write-Host "[!] Virtual environment not found, attempting system Python..." -ForegroundColor Yellow
    python server.py $args
}

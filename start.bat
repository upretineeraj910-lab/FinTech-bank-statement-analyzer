@echo off
echo ========================================================
echo  FinTech Bank Statement Analyzer - Launcher
echo ========================================================
echo.

if exist "venv\Scripts\python.exe" (
    echo [OK] Using virtual environment Python...
    venv\Scripts\python.exe server.py %*
) else (
    echo [!] Virtual environment not found, attempting system Python...
    python server.py %*
)

pause

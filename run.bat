@echo off
cd /d "%~dp0"
python main.py
if errorlevel 1 (
    echo.
    echo ERROR: Could not run the app.
    echo Make sure Python is installed and you have run:
    echo   pip install -r requirements.txt
    pause
)

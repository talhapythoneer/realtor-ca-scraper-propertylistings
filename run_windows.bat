@echo off
REM Convenience launcher for Windows: double-click to run the scraper with default settings.
REM For custom options (e.g. a catch-up run), open a terminal instead and run:
REM   python run_scraper.py --days-back 3
cd /d "%~dp0"
python run_scraper.py %*
pause

@echo off
REM ============================================================
REM  One-time setup for the realtor.ca scraper.
REM  Double-click this file once before you use the scraper for
REM  the first time. You do not need to know anything about
REM  command lines or Python to run this - just double-click it
REM  and follow along.
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo  realtor.ca scraper - one-time setup
echo ============================================================
echo.

REM --- Step 1: check Python is installed -----------------------
echo Step 1 of 3: checking for Python...
where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   PROBLEM: Python was not found on this computer.
    echo.
    echo   Please install Python first:
    echo     1. Go to https://www.python.org/downloads/
    echo     2. Download and run the installer.
    echo     3. IMPORTANT: on the first install screen, tick the box
    echo        that says "Add Python to PATH" before clicking Install.
    echo     4. Once it finishes, double-click this setup_windows.bat file again.
    echo.
    pause
    exit /b 1
)
python --version
echo   OK - Python is installed.
echo.

REM --- Step 2: check Google Chrome is installed ------------------
echo Step 2 of 3: checking for Google Chrome...
set "CHROME_FOUND=0"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"

if "%CHROME_FOUND%"=="1" (
    echo   OK - Google Chrome is installed.
) else (
    echo.
    echo   WARNING: Could not find Google Chrome in the usual place.
    echo   This scraper needs Google Chrome to work - it opens a
    echo   real Chrome window and drives it automatically.
    echo.
    echo   If Chrome is not installed, get it free from:
    echo     https://www.google.com/chrome/
    echo.
    echo   If you already have Chrome installed somewhere unusual,
    echo   you can ignore this warning and continue.
    echo.
)
echo.

REM --- Step 3: install the required Python packages --------------
echo Step 3 of 3: installing required packages (this can take a few minutes)...
echo.
REM Upgrading setuptools/wheel here avoids a "No module named distutils" error
REM on newer Python versions (3.12+), which dropped distutils from the standard
REM library - current setuptools ships its own replacement for it.
python -m pip install --upgrade pip setuptools wheel >nul
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   PROBLEM: something went wrong installing the required packages.
    echo   Scroll up to see the error message, or send it to whoever
    echo   set this up for you.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Setup complete!
echo.
echo  Next steps:
echo    1. Open the input folder and check input.csv has the
echo       cities you want (it already comes pre-filled).
echo    2. Double-click run_windows.bat to run the scraper.
echo    3. Your results will appear in the output folder.
echo.
echo  You only need to run this setup_windows.bat file once. From now on,
echo  just use run_windows.bat whenever you want new listings.
echo ============================================================
echo.
pause

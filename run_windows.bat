@echo off
REM Double-click this file any time you want to scrape new listings.
REM First time using this scraper? Run setup.bat once before this.
REM For advanced one-off options (e.g. --days-back, --dry-run), open a
REM terminal instead and run: python run_scraper.py --days-back 3
cd /d "%~dp0"

echo.
echo Starting the realtor.ca scraper...
echo A Chrome window will open on its own in a moment - leave it alone
echo and let it run. This can take a while depending on how many cities
echo and new listings there are.
echo.

python run_scraper.py %*

echo.
echo Finished. Check the "output" folder for your results.
pause

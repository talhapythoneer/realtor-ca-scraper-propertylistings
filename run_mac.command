#!/bin/bash
# Double-click this file any time you want to scrape new listings.
# First time using this scraper? Run setup_mac.command once before this.
# For advanced one-off options (e.g. --days-back, --dry-run), open
# Terminal instead and run: python3 run_scraper.py --days-back 3
#
# If macOS blocks this with a "cannot verify / malware" warning the
# first time, see the "Mac security warning" section in README.md -
# the fix you did for setup_mac.command covers this file too if you ran
# the xattr command on both at once.

cd "$(dirname "$0")"

PYTHON_CMD=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_CMD="$candidate"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "Python was not found. Run setup_mac.command first."
    read -n 1 -s -r -p "Press any key to close this window..."
    echo ""
    exit 1
fi

echo ""
echo "Starting the realtor.ca scraper..."
echo "A Chrome window will open on its own in a moment - leave it alone"
echo "and let it run. This can take a while depending on how many cities"
echo "and new listings there are."
echo ""

"$PYTHON_CMD" run_scraper.py "$@"

echo ""
echo "Finished. Check the \"output\" folder for your results."
read -n 1 -s -r -p "Press any key to close this window..."
echo ""

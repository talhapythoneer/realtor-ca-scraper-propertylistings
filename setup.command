#!/bin/bash
# ============================================================
#  One-time setup for the realtor.ca scraper (macOS).
#  Double-click this file once before you use the scraper for
#  the first time. You do not need to know anything about
#  the command line or Python to run this - just double-click
#  it and follow along.
#
#  The first time you try to open this, macOS will likely block it
#  with a warning that looks like a malware detection - that's
#  normal for any unsigned script, not a real detection. See the
#  "Mac security warning" section in README.md for the fix (short
#  version: right-click this file and choose "Open" instead of
#  double-clicking; if that doesn't offer an Open button, see
#  README.md for the next step).
# ============================================================

cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo " realtor.ca scraper - one-time setup"
echo "============================================================"
echo ""

# --- Step 1: find Python 3 ------------------------------------
echo "Step 1 of 3: checking for Python..."
PYTHON_CMD=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            PYTHON_CMD="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo ""
    echo "  PROBLEM: Python 3.10 or newer was not found on this Mac."
    echo ""
    echo "  Please install Python first:"
    echo "    1. Go to https://www.python.org/downloads/"
    echo "    2. Download and run the macOS installer."
    echo "    3. Once it finishes, double-click this setup.command file again."
    echo ""
    read -n 1 -s -r -p "Press any key to close this window..."
    echo ""
    exit 1
fi

"$PYTHON_CMD" --version
echo "  OK - Python is installed ($PYTHON_CMD)."
echo ""

# The official python.org installer ships its own OpenSSL instead of using
# macOS's trust store, so HTTPS requests fail with "certificate verify
# failed" until a one-time fix script (that installer leaves behind) is run.
# Run it automatically here if it's present - harmless to run again if it
# was already done, and does nothing if Python came from somewhere else
# (Homebrew, etc.) that doesn't have this issue in the first place.
PY_MAJOR_MINOR="$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)"
CERT_SCRIPT="/Applications/Python $PY_MAJOR_MINOR/Install Certificates.command"
if [ -n "$PY_MAJOR_MINOR" ] && [ -f "$CERT_SCRIPT" ]; then
    echo "Installing SSL certificates needed for Python (one-time macOS fix)..."
    "$CERT_SCRIPT" >/dev/null 2>&1
    echo "  OK."
    echo ""
fi

# --- Step 2: check Google Chrome is installed -------------------
echo "Step 2 of 3: checking for Google Chrome..."
if [ -d "/Applications/Google Chrome.app" ]; then
    echo "  OK - Google Chrome is installed."
else
    echo ""
    echo "  WARNING: Could not find Google Chrome in /Applications."
    echo "  This scraper needs Google Chrome to work - it opens a"
    echo "  real Chrome window and drives it automatically."
    echo ""
    echo "  If Chrome is not installed, get it free from:"
    echo "    https://www.google.com/chrome/"
    echo ""
    echo "  If you already have Chrome installed somewhere unusual,"
    echo "  you can ignore this warning and continue."
    echo ""
fi
echo ""

# --- Step 3: install the required Python packages ----------------
echo "Step 3 of 3: installing required packages (this can take a few minutes)..."
echo ""
# Upgrading setuptools/wheel here avoids a "No module named distutils" error
# on newer Python versions (3.12+), which dropped distutils from the standard
# library - current setuptools ships its own replacement for it.
"$PYTHON_CMD" -m pip install --upgrade pip setuptools wheel >/dev/null
"$PYTHON_CMD" -m pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo ""
    echo "  PROBLEM: something went wrong installing the required packages."
    echo "  Scroll up to see the error message, or send it to whoever"
    echo "  set this up for you."
    echo ""
    read -n 1 -s -r -p "Press any key to close this window..."
    echo ""
    exit 1
fi

echo ""
echo "============================================================"
echo " Setup complete!"
echo ""
echo " Next steps:"
echo "   1. Open the input folder and check input.csv has the"
echo "      cities you want (it already comes pre-filled)."
echo "   2. Double-click run_mac.command to run the scraper."
echo "   3. Your results will appear in the output folder."
echo ""
echo " You only need to run this setup.command file once. From now"
echo " on, just use run_mac.command whenever you want new listings."
echo "============================================================"
echo ""
read -n 1 -s -r -p "Press any key to close this window..."
echo ""

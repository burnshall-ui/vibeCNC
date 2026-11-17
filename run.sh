#!/bin/bash
# Vibe CNC start script (macOS/Linux)

set -e

cd "$(dirname "$0")"

# Use an existing .venv (what editors and most tools create) before falling
# back to venv, so a second environment is not built next to the first one.
VENV=venv
if [ -d ".venv" ]; then
    VENV=.venv
fi

# Create the virtual environment if it does not exist
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV"
fi

# Activate
source "$VENV/bin/activate"

# Install dependencies
echo "Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Start the app
echo "Starting Vibe CNC..."
python vibe_cnc.py

#!/bin/bash
# Vibe CNC start script (macOS/Linux)

set -e

cd "$(dirname "$0")"

# Create the virtual environment if it does not exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Start the app
echo "Starting Vibe CNC..."
python vibe_cnc.py

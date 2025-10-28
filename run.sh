#!/bin/bash
# Vibe CNC Start-Script (macOS/Linux)

set -e

cd "$(dirname "$0")"

# Virtuelle Umgebung erstellen falls nicht vorhanden
if [ ! -d "venv" ]; then
    echo "Erstelle virtuelle Umgebung..."
    python3 -m venv venv
fi

# Aktivieren
source venv/bin/activate

# Dependencies installieren
echo "Installiere Dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# App starten
echo "Starte Vibe CNC..."
python vibe_cnc.py

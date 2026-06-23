#!/bin/bash

# Get the absolute path of the project directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
DESKTOP_FILE="$HOME/Desktop/BMO_Silent.desktop"

echo "Creating Silent (Headless) desktop shortcut for BMO..."

# Create the launcher script
cat << 'EOF' > "$PROJECT_DIR/run_bmo_silent.sh"
#!/bin/bash
cd "$(dirname "$0")"

# ── Display routing ────────────────────────────────────────────────────────────
# Always render on the Pi's physical screen, even when called from SSH.
export DISPLAY=:0
export XAUTHORITY="$HOME/.Xauthority"

# ── Virtual environment ────────────────────────────────────────────────────────
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# ── Run BMO silently (all output goes to log file) ────────────────────────────
python3 main.py >> "$PROJECT_DIR/data/logs/terminal_output.log" 2>&1
EOF

# Make the launcher executable
chmod +x "$PROJECT_DIR/run_bmo_silent.sh"

# Create the .desktop file
cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Name=BMO Robot (Silent)
Comment=Start the BMO Robot in the background
Exec="$PROJECT_DIR/run_bmo_silent.sh"
Icon=face-smile
Terminal=false
Type=Application
Categories=Education;
Path=$PROJECT_DIR
EOF

# Make the desktop shortcut executable
chmod +x "$DESKTOP_FILE"

echo "Done! You can now double-click the 'BMO Robot (Silent)' icon on your Pi desktop."
echo "It will start completely in the background without opening a terminal window."
echo "Any errors will be saved to: data/logs/terminal_output.log"

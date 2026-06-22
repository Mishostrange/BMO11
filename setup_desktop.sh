#!/bin/bash

# Get the absolute path of the project directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
DESKTOP_FILE="$HOME/Desktop/BMO.desktop"

echo "Creating desktop shortcut for BMO..."

# Create the launcher script
cat << 'EOF' > "$PROJECT_DIR/run_bmo.sh"
#!/bin/bash
cd "$(dirname "$0")"

echo "Starting BMO..."

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Run the application
python3 main.py

# Keep terminal open if it crashes
if [ $? -ne 0 ]; then
    echo "BMO exited with an error. Press enter to close..."
    read
fi
EOF

# Make the launcher executable
chmod +x "$PROJECT_DIR/run_bmo.sh"

# Create the .desktop file
cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Name=BMO Robot
Comment=Start the BMO Autism Therapy Robot
Exec=lxterminal -e "$PROJECT_DIR/run_bmo.sh"
Icon=face-smile
Terminal=false
Type=Application
Categories=Education;
Path=$PROJECT_DIR
EOF

# Make the desktop shortcut executable
chmod +x "$DESKTOP_FILE"

echo "Done! You can now double-click the 'BMO Robot' icon on your Raspberry Pi desktop to start the application."

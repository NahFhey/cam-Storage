#!/bin/bash
# CAM Tracking Kiosk - Startup Script for Raspberry Pi
# This script starts the FastAPI server and launches Chromium in kiosk mode

# Configuration
APP_DIR="/home/pi/cam-tracking"
PYTHON_BIN="/usr/bin/python3"
SERVER_PORT="8000"
STARTUP_DELAY=5

# Change to application directory
cd "$APP_DIR" || exit 1

# Kill any existing server processes
pkill -f "python3.*main.py"

# Start the FastAPI server in background
echo "Starting CAM Tracking server..."
$PYTHON_BIN main.py > cam_tracking.log 2>&1 &

# Wait for server to start
echo "Waiting for server to start..."
sleep $STARTUP_DELAY

# Check if server is running
if ! pgrep -f "python3.*main.py" > /dev/null; then
    echo "ERROR: Server failed to start. Check cam_tracking.log"
    exit 1
fi

# Disable screen blanking and screensaver (if running in X)
if [ -n "$DISPLAY" ]; then
    xset s off
    xset -dpms
    xset s noblank
fi

# Hide mouse cursor (install unclutter if needed: sudo apt-get install unclutter)
if command -v unclutter &> /dev/null; then
    unclutter -idle 0.5 -root &
fi

# Launch Chromium in kiosk mode
echo "Launching Chromium in kiosk mode..."
chromium-browser \
    --kiosk \
    --start-fullscreen \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-component-update \
    --no-first-run \
    --disable-translate \
    --disable-features=TranslateUI \
    --disk-cache-dir=/dev/null \
    --password-store=basic \
    http://localhost:$SERVER_PORT

# If Chromium exits, kill the server
echo "Chromium closed. Stopping server..."
pkill -f "python3.*main.py"

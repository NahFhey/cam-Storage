#!/bin/bash
# CAM Tracking Kiosk - opens the app full-screen in Chromium.
#
# Run this from the desktop session's autostart (see INSTALL_RASPBERRY_PI.md).
# The server normally runs as the cam-tracking systemd service; this script
# waits for it. Without that service it starts the server itself and stops
# it again when the browser closes.
#
# Overridable: APP_DIR, PORT, KIOSK_URL, SERVER_TIMEOUT (seconds).

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")" && pwd)}"
PORT="${PORT:-8000}"
KIOSK_URL="${KIOSK_URL:-http://localhost:$PORT/static/index.html}"
SERVER_TIMEOUT="${SERVER_TIMEOUT:-60}"
HEALTH_URL="http://localhost:$PORT/health"

server_up() {
    curl -fsS --max-time 2 "$HEALTH_URL" > /dev/null 2>&1
}

started_server=""
if ! server_up; then
    if systemctl is-enabled --quiet cam-tracking 2> /dev/null; then
        echo "Waiting for the cam-tracking service..."
    else
        PYTHON_BIN="$APP_DIR/venv/bin/python"
        [ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"
        echo "Starting CAM Tracking server with $PYTHON_BIN..."
        cd "$APP_DIR" || exit 1
        PORT="$PORT" "$PYTHON_BIN" main.py > /dev/null 2>> "$APP_DIR/server_console.log" &
        started_server=$!
    fi
fi

for _ in $(seq "$SERVER_TIMEOUT"); do
    server_up && break
    sleep 1
done
if ! server_up; then
    echo "ERROR: server not reachable at $HEALTH_URL after ${SERVER_TIMEOUT}s." >&2
    echo "Check: journalctl -u cam-tracking  (or $APP_DIR/server_console.log)" >&2
    [ -n "$started_server" ] && kill "$started_server" 2> /dev/null
    exit 1
fi

# X11 sessions only: stop screen blanking and hide the idle mouse pointer.
# On Wayland (the default desktop) turn off Screen Blanking in raspi-config.
if [ -n "$DISPLAY" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    if command -v xset > /dev/null; then
        xset s off
        xset -dpms
        xset s noblank
    fi
    command -v unclutter > /dev/null && unclutter -idle 0.5 -root &
fi

BROWSER="$(command -v chromium || command -v chromium-browser)"
if [ -z "$BROWSER" ]; then
    echo "ERROR: Chromium not found. Install it: sudo apt install chromium" >&2
    [ -n "$started_server" ] && kill "$started_server" 2> /dev/null
    exit 1
fi

# --disk-cache-dir=/dev/null: a browser restart always loads the current UI after an update
"$BROWSER" \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --no-first-run \
    --disable-translate \
    --disable-features=TranslateUI \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --disk-cache-dir=/dev/null \
    --password-store=basic \
    "$KIOSK_URL"

if [ -n "$started_server" ]; then
    echo "Chromium closed. Stopping server..."
    kill "$started_server" 2> /dev/null
fi

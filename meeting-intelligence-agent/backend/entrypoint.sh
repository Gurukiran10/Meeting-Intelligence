#!/bin/bash
# Clean up stale X lock files so Xvfb starts cleanly on container restart
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

# Start Xvfb virtual display (headed Chromium bypasses Google's headless detection)
Xvfb :99 -screen 0 1280x720x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
export DISPLAY=:99

# Give Xvfb 2s to initialise
sleep 2

# Verify Xvfb started
if ! kill -0 $XVFB_PID 2>/dev/null; then
  echo "[entrypoint] WARNING: Xvfb failed to start — falling back to headless mode"
  export MEET_BOT_HEADLESS=1
fi

# Start PulseAudio for audio capture (non-fatal if unavailable)
if command -v pulseaudio &>/dev/null; then
  pulseaudio --start --log-target=syslog 2>/dev/null || true
fi

exec "$@"

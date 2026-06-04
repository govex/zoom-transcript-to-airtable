#!/bin/bash
# setup_mac.sh — installs a daily cron job on Mac
# Run this once after cloning the repo and filling in .env

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"
SCRIPT="$SCRIPT_DIR/zoom_to_airtable.py"
LOG="$SCRIPT_DIR/zoom_to_airtable.log"

# ── Change this if you want a different run time ───────────────────────────
RUN_HOUR=21   # 9 PM local time
# ──────────────────────────────────────────────────────────────────────────

if [ ! -f "$SCRIPT_DIR/.env" ]; then
  # Note: ffmpeg is not required — faster-whisper bundles its own audio libraries.
  echo "❌  No .env file found. Copy .env.example to .env and add your Airtable token first."
  exit 1
fi

CRON_LINE="0 $RUN_HOUR * * * $PYTHON $SCRIPT >> $LOG 2>&1"

# Check if already installed
if crontab -l 2>/dev/null | grep -qF "zoom_to_airtable.py"; then
  echo "⚠️  A zoom_to_airtable cron job already exists:"
  crontab -l | grep "zoom_to_airtable.py"
  echo ""
  echo "To remove it: crontab -e   (delete the line manually)"
  exit 0
fi

# Install
(crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -

echo "✅  Cron job installed — runs daily at ${RUN_HOUR}:00 local time"
echo "    Script : $SCRIPT"
echo "    Log    : $LOG"
echo ""
echo "To check it's there : crontab -l"
echo "To remove it        : crontab -e   (delete the line)"

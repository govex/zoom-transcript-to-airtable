#!/bin/bash
# setup_mac.sh — installs a daily launchd agent on Mac
#
# Uses launchd (not cron) so the job runs even if the laptop was asleep or in
# clamshell at the scheduled time — it fires the next time the machine wakes.
#
# Run this once after cloning the repo and filling in .env

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"
SCRIPT="$SCRIPT_DIR/zoom_to_airtable.py"
LOG="$SCRIPT_DIR/zoom_to_airtable.log"
PLIST_LABEL="com.govex.zoom-transcript-to-airtable"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

# ── Change this if you want a different run time ───────────────────────────
RUN_HOUR=21   # 9 PM local time
RUN_MINUTE=0
# ──────────────────────────────────────────────────────────────────────────

if [ ! -f "$SCRIPT_DIR/.env" ]; then
  echo "❌  No .env file found. Copy .env.example to .env and add your Airtable token first."
  exit 1
fi

# Check if already installed
if [ -f "$PLIST_PATH" ]; then
  echo "⚠️  A launchd agent is already installed at:"
  echo "    $PLIST_PATH"
  echo ""
  echo "To remove it:"
  echo "    launchctl unload $PLIST_PATH"
  echo "    rm $PLIST_PATH"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"

# Write the plist
cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$RUN_HOUR</integer>
        <key>Minute</key>
        <integer>$RUN_MINUTE</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
</dict>
</plist>
PLIST

# Load the agent
launchctl load "$PLIST_PATH"

echo "✅  launchd agent installed — runs daily at ${RUN_HOUR}:$(printf '%02d' $RUN_MINUTE) local time"
echo "    Plist  : $PLIST_PATH"
echo "    Log    : $LOG"
echo ""
echo "ℹ️  If the laptop is asleep at ${RUN_HOUR}:$(printf '%02d' $RUN_MINUTE), the job runs the next time it wakes."
echo ""
echo "To check status : launchctl list | grep govex"
echo "To run now      : launchctl start $PLIST_LABEL"
echo "To remove       : launchctl unload $PLIST_PATH && rm $PLIST_PATH"

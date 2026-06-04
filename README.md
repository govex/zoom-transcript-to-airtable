# Zoom Transcript → Airtable Automation

Automatically transcribes Zoom local recordings and populates CDA meeting report records in Airtable. Runs once a day in the background — no manual steps after setup.

---

## How it works

Each night the script:
1. Scans `~/Documents/Zoom` for new recording folders
2. Groups sessions by meeting title + date (handles meetings that end and restart)
3. Skips sessions outside the meeting's scheduled window (prevents cross-contamination from back-to-back meetings)
4. Transcribes audio locally using Whisper — **audio never leaves your machine**
5. Combines the call transcript and chat log with timestamps
6. Matches the recording to the correct Airtable meeting record by title
7. Creates a new report record with the transcript, event type, and attendee info

---

## Setup

> All terminal/PowerShell commands below assume you have navigated into the repo folder first:
> ```bash
> cd path/to/transcripts-zoom-to-airtable
> ```

### Step 1 — Prerequisites

**Mac:**

First, check if Python 3 is installed:
```bash
python3 --version
```
If you get `command not found`, install it:
```bash
# Option A — via Homebrew (recommended)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python

# Option B — direct download
# Go to https://www.python.org/downloads/ and install Python 3.9 or later
```

Then install the dependencies:
```bash
pip3 install -r requirements.txt
```

**Windows:**

First, check if Python is installed:
```powershell
python --version
```
If you get an error, install it:
1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download Python 3.9 or later
3. Run the installer — **check "Add Python to PATH"** before clicking Install

Then install the dependencies:
```powershell
pip install -r requirements.txt
```

> **No ffmpeg needed** — faster-whisper bundles its own audio decoding libraries.

> **First run note:** faster-whisper will automatically download the `medium` model (~1.5 GB) the first time it transcribes. This is a one-time download. The script also auto-detects whether a CUDA GPU is available and uses it if so, otherwise falls back to CPU.

---

### Step 2 — Get your Airtable token

1. Go to [airtable.com/create/tokens](https://airtable.com/create/tokens)
2. Create a Personal Access Token with **read and write** access to the CDA base
3. Copy the token — you'll need it in the next step

---

### Step 3 — Add your credentials

Copy the example file and fill in your token:

**Mac:**
```bash
cp .env.example .env
open -e .env   # opens in TextEdit
```

**Windows:**
```powershell
Copy-Item .env.example .env
notepad .env
```

The file should look like:
```
AIRTABLE_TOKEN=your_token_here
```

---

### Step 4 — Install the daily schedule

**Mac:**
```bash
chmod +x setup_mac.sh
./setup_mac.sh
```

This installs a **launchd agent** (not cron). The key difference: if your laptop is asleep or in clamshell at the scheduled time, launchd will run the job the next time the machine wakes up. Cron would silently skip it.

**Windows** — right-click `setup_windows.ps1` → **Run with PowerShell**
(or from a PowerShell prompt: `.\setup_windows.ps1`)

By default the script runs at **9 PM local time**. To change it, open the setup script and edit the `RUN_HOUR` (Mac) or `$RunTime` (Windows) line before running.

---

## Running manually

Any time you want to trigger it immediately, navigate to the repo folder first, then:

**Mac:**
```bash
cd path/to/transcripts-zoom-to-airtable
./run.sh
```

**Windows:**
```powershell
cd path\to\transcripts-zoom-to-airtable
python zoom_to_airtable.py
```

Logs are written to `zoom_to_airtable.log` in this folder.

After a successful upload, the script writes a `transcript_uploaded.txt` file inside each processed Zoom recording folder. This file means two things:
- The script will skip that folder on future runs
- **It is safe to delete that recording folder**

---

## Zoom settings

### Required — local recording

These must be on for the script to have anything to process.

> **Zoom → Settings → Recording**
> - ✅ **Local Recording** → ON
> - ✅ **Store my recordings at** → leave as default (`~/Documents/Zoom`)
> - ✅ **Record a local copy automatically when joining a meeting** → ON *(optional but recommended — means you never forget to hit Record)*
> - ✅ **Separate audio file for each participant** → ON *(improves transcription quality)*

---

## Fallback — manually placing a cloud transcript

If a meeting was recorded to the cloud and you have access to the Zoom transcript, you can download the VTT from [zoom.us/recording](https://zoom.us/recording) and drop it into the local recording folder. The script will detect it, use the Zoom transcript with timestamps, and skip Whisper entirely.

**Steps:**
1. Find the meeting's local recording folder in `~/Documents/Zoom/`
2. Place the downloaded `.vtt` file inside that folder
3. Run the script as normal

The output will look like:
```
[0:02 → 0:14]  Attendee A: Alright, let's get started.
[0:14 → 0:22]  Attendee B: Sorry, just joining now.
```

---

## Customising event type detection

The script infers event type from the meeting title using keyword rules at the top of `zoom_to_airtable.py`. Edit `EVENT_TYPE_RULES` to match your naming conventions.

Current mappings:
| Event type | Matched keywords |
|---|---|
| Coworking Session | coworking, co-working, work session |
| Activation Session | activation |
| Peer Sharing Session | peer sharing, peer session, learning session |
| CDA Staff Meeting | staff meeting, managers meeting, team meeting, cda staff, internal sync |
| Strategy Call | strategy, check-in, coaching, office hours, debrief, planning |
| *(default)* | Strategy Call |

---

## Troubleshooting

**"No Airtable record found for '...'"**
The meeting title in Zoom must match the `Title` field in the Airtable calendar — check for typos or extra spaces in the Zoom meeting name.

**"AIRTABLE_TOKEN not set"**
Make sure you copied `.env.example` to `.env` and filled in your token.

**Whisper is slow**
Switch to the `small` model by changing `WHISPER_MODEL = "medium"` to `WHISPER_MODEL = "small"` in `zoom_to_airtable.py`. Faster but slightly less accurate.

**Windows: script won't run / execution policy error**
Open PowerShell as administrator and run:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

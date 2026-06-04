#!/usr/bin/env python3
"""
zoom_to_airtable.py — Zoom local recordings → faster-whisper → Airtable

For each meeting found in ~/Documents/Zoom:
  1. Groups recording folders by title + date (handles meetings that end/restart)
  2. Filters sessions to those within the Airtable-scheduled window + 30 min buffer
  3. Transcribes audio with faster-whisper (or reads existing VTT if Zoom made one)
  4. Combines call + chat transcripts, with SESSION headers if multiple sessions
  5. Matches the Airtable meeting record by title + date
  6. Infers event type from the meeting title
  7. Resolves attendees → delivery staff (linked records) vs city staff (emails)
  8. Creates or updates the report record in Airtable

Expected Zoom folder name format: "YYYY-MM-DD HH.MM.SS Meeting Title"
"""

import os
import re
import json
import sys
from faster_whisper import WhisperModel
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ─── Load .env ───────────────────────────────────────────────────────────────
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ─── Config ──────────────────────────────────────────────────────────────────
AIRTABLE_TOKEN      = os.environ.get("AIRTABLE_TOKEN", "")
BASE_ID             = "appbRMatpdHgkk7SH"
MEETINGS_TABLE      = "tblsr1Wtvm5vqQXN9"   # Calendar events — record created when meeting is scheduled
REPORTS_TABLE       = "tbl9hf0LizhA1D3o1"   # Meeting reports — we create the transcript record here
TRANSCRIPT_FIELD    = "fldTUUCN7SdznyQYv"   # "Notes" field in reports table
CITY_STAFF_FIELD    = "fldmLkd5P4Y392xMI"   # Email text field for non-delivery attendees
SELECT_EVENT_FIELD  = "Select Event"         # Linked record back to MEETINGS_TABLE
WHISPER_MODEL       = "medium"

ZOOM_DIR            = Path.home() / "Documents" / "Zoom"
PROCESSED_LOG       = Path(__file__).parent / ".processed.json"

# Extra buffer before/after the scheduled meeting window to catch sessions that
# start slightly early or run over before a restart.
WINDOW_PRE_BUFFER   = timedelta(minutes=10)
WINDOW_POST_BUFFER  = timedelta(minutes=30)
WINDOW_DEFAULT_DUR  = timedelta(hours=2)    # used when Airtable has no End time

# ─── Airtable helpers ────────────────────────────────────────────────────────

def _headers():
    return {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json"
    }

def at_get(table, record_id_or_params=None, params=None):
    """Fetch a single record (pass record_id string) or list records (pass params dict)."""
    if isinstance(record_id_or_params, str):
        url = f"https://api.airtable.com/v0/{BASE_ID}/{table}/{record_id_or_params}"
        r = requests.get(url, headers=_headers())
    else:
        url = f"https://api.airtable.com/v0/{BASE_ID}/{table}"
        r = requests.get(url, headers=_headers(), params=record_id_or_params or {})
    r.raise_for_status()
    return r.json()

def at_patch(table, record_id, fields):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table}/{record_id}"
    r = requests.patch(url, headers=_headers(), json={"fields": fields})
    r.raise_for_status()
    return r.json()

def at_create(table, fields):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table}"
    r = requests.post(url, headers=_headers(), json={"fields": fields})
    r.raise_for_status()
    return r.json()

# ─── Time helpers ─────────────────────────────────────────────────────────────

def utc_to_local(utc_str):
    """Convert an Airtable UTC timestamp string to a naive local datetime."""
    dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    return dt.astimezone().replace(tzinfo=None)

# ─── Zoom folder helpers ─────────────────────────────────────────────────────

def parse_folder(name):
    """Return (datetime, title) from '2026-06-03 16.29.51 Meeting Title', or (None, None)."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}) (.+)$", name)
    if not m:
        return None, None
    dt = datetime.strptime(m.group(1), "%Y-%m-%d %H.%M.%S")
    return dt, m.group(2).strip()

def find_audio(folder):
    """Prefer .m4a (audio-only, smaller), fall back to .mp4."""
    for ext in ("*.m4a", "*.mp4"):
        files = list(folder.glob(ext))
        if files:
            return files[0]
    return None

def _vtt_ts_to_seconds(ts):
    """Convert a VTT timestamp like '00:01:23.456' or '01:23.456' to seconds (int)."""
    ts = ts.split(".")[0].strip()          # drop milliseconds
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(parts[0]) * 60 + int(parts[1])

def read_vtt(folder):
    """
    If Zoom saved a VTT or closed caption file, return a formatted transcript.

    Two modes detected automatically:

    1. Cloud/AI Companion VTT — lines contain speaker names ("Rachel Chen: ...")
       → formatted as timestamped lines matching Whisper output:
           [0:02 → 0:14]  Rachel Chen: Alright, I think we're all here.
       → Whisper is NOT run; speaker names and times are preserved.

    2. Local live-caption VTT / closed_caption.txt — no speaker names
       → plain text, formatting stripped.

    Returns None if no VTT file is present.
    """
    for pat in ("*.vtt", "closed_caption.txt"):
        files = list(folder.glob(pat))
        if not files:
            continue

        raw = files[0].read_text()

        # Parse into (start_ts, end_ts, text) segments
        segments = []
        current_ts = (None, None)
        current_lines = []

        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("WEBVTT") or re.match(r"^\d+$", line):
                continue
            if "-->" in line:
                if current_lines:
                    segments.append((current_ts, " ".join(current_lines)))
                parts = line.split("-->")
                current_ts = (parts[0].strip(), parts[1].strip())
                current_lines = []
            else:
                current_lines.append(line)

        if current_lines:
            segments.append((current_ts, " ".join(current_lines)))

        if not segments:
            return None

        # Detect speaker-labelled VTT (cloud/AI Companion download)
        speaker_re = re.compile(r"^[A-Z][^:]{1,40}: .+")
        has_speakers = any(speaker_re.match(text) for _, text in segments)

        if has_speakers:
            print("  ✓  Cloud VTT detected — using Zoom transcript with speaker labels")
            lines = []
            for (start, end), text in segments:
                s = _vtt_ts_to_seconds(start)
                e = _vtt_ts_to_seconds(end)
                lines.append(f"[{_fmt_ts(s)} → {_fmt_ts(e)}]  {text}")
            return "\n".join(lines)
        else:
            return " ".join(t for _, t in segments).strip()

    return None

def read_chat(folder):
    """Read chat.txt if present, else return None."""
    chat = folder / "chat.txt"
    return chat.read_text().strip() if chat.exists() else None

def _fmt_ts(seconds):
    """Format seconds as M:SS or H:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def _best_device():
    """
    Return (device, compute_type) for the fastest available hardware.

    faster-whisper does NOT support MPS/Metal on Apple Silicon — CUDA is the
    only GPU path. On M2 Mac the CPU path uses Apple's Accelerate framework
    automatically, so it's still faster than stock openai-whisper on CPU.

    Detection uses ctranslate2 (bundled with faster-whisper — always available).
    """
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"

# Module-level model cache — loaded once per script run, reused across sessions
_whisper_model_cache = None

def _get_model():
    global _whisper_model_cache
    if _whisper_model_cache is None:
        device, compute_type = _best_device()
        print(f"    Loading faster-whisper ({WHISPER_MODEL}, {device}/{compute_type}) ...")
        _whisper_model_cache = WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type)
    return _whisper_model_cache

def transcribe_audio(audio_path, session_label=""):
    """
    Transcribe audio with faster-whisper and return a timestamped transcript.
    Each segment is formatted as:
        [0:00 → 0:14]  Hello, this is a test...
    """
    prefix = f"    [{session_label}] " if session_label else "    "
    print(f"{prefix}🎙  Transcribing {audio_path.name} ...")
    model = _get_model()
    segments, _ = model.transcribe(str(audio_path))
    lines = []
    for seg in segments:                      # generator — consumed once
        lines.append(f"[{_fmt_ts(seg.start)} → {_fmt_ts(seg.end)}]  {seg.text.strip()}")
    return "\n".join(lines)

def build_combined(call_text, chat_text):
    """Combine call and chat transcripts with labelled headers for the downstream AI agent."""
    parts = [f"CALL TRANSCRIPT\n\n{call_text}"]
    if chat_text:
        parts.append(f"CHAT TRANSCRIPT\n\n{chat_text}")
    return "\n\n---\n\n".join(parts)

# ─── Session grouping ─────────────────────────────────────────────────────────

def group_folders(folders):
    """
    Group unprocessed Zoom folders by (date, title).
    Returns dict: {("YYYY-MM-DD", "Title"): [(datetime, Path), ...]}
    Each group is sorted by time (ascending).
    """
    groups = defaultdict(list)
    for folder in folders:
        dt, title = parse_folder(folder.name)
        if dt and title:
            key = (dt.strftime("%Y-%m-%d"), title)
            groups[key].append((dt, folder))
    for key in groups:
        groups[key].sort(key=lambda x: x[0])
    return groups

def filter_to_window(sessions, meeting_record):
    """
    Keep only sessions whose start time falls within:
      [Airtable Start − 5 min,  Airtable End + 30 min]

    This prevents merging recordings from back-to-back same-named meetings
    on the same day. If Airtable has no End time, assumes a 2-hour meeting.
    """
    start_str = meeting_record["fields"].get("Start", "")
    end_str   = meeting_record["fields"].get("End", "")

    if not start_str:
        return sessions   # no schedule info — keep all

    window_start = utc_to_local(start_str) - WINDOW_PRE_BUFFER
    if end_str:
        window_end = utc_to_local(end_str) + WINDOW_POST_BUFFER
    else:
        window_end = utc_to_local(start_str) + WINDOW_DEFAULT_DUR + WINDOW_POST_BUFFER

    filtered = [(dt, f) for dt, f in sessions if window_start <= dt <= window_end]

    dropped = len(sessions) - len(filtered)
    if dropped:
        print(f"  ℹ️  {dropped} session(s) outside meeting window — excluded")

    return filtered

# ─── Event type detection ─────────────────────────────────────────────────────

# First match wins — put more specific phrases before broad ones.
EVENT_TYPE_RULES = [
    ("Coworking Session",    ["coworking", "co-working", "work session"]),
    ("Activation Session",   ["activation"]),
    ("Peer Sharing Session", ["peer sharing", "peer session", "learning session"]),
    ("CDA Staff Meeting",    ["staff meeting", "managers meeting", "team meeting",
                              "cda staff", "internal sync"]),
    ("Strategy Call",        ["strategy", "check-in", "check in", "coaching",
                              "office hours", "debrief", "planning"]),
]
EVENT_TYPE_DEFAULT = "Strategy Call"

def infer_event_type(title):
    """Return the best-matching event type label for a meeting title."""
    lower = title.lower()
    for event_type, keywords in EVENT_TYPE_RULES:
        if any(kw in lower for kw in keywords):
            return event_type
    return EVENT_TYPE_DEFAULT

# ─── Attendee resolution ──────────────────────────────────────────────────────

def resolve_attendees(meeting_record):
    """
    Walk the meeting's Attendees linked records to produce:
      delivery_staff_ids — record IDs for person records in the delivery org
      city_staff_emails  — email strings for attendees not in the delivery org

    Data structure in MEETINGS_TABLE:
      Meeting  →  Attendees  (records where Name = email address)
                    Skip = True          → tracking email, ignore
                    Program Staff = [id] → delivery staff person record IDs
                    (no Program Staff)   → city staff; save their email
    """
    delivery_staff_ids = []
    city_staff_emails  = []

    seen_orgs = set()

    for attendee_id in meeting_record["fields"].get("Attendees", []):
        rec    = at_get(MEETINGS_TABLE, attendee_id)
        fields = rec["fields"]

        if fields.get("Skip"):
            continue

        ps = fields.get("Program Staff", [])
        if ps:
            for staff_id in ps:
                # Fetch the person record to get their org, dedupe by org
                staff = at_get(MEETINGS_TABLE, staff_id)
                org   = staff["fields"].get("Organization", "").strip()
                if org not in seen_orgs:
                    seen_orgs.add(org)
                    delivery_staff_ids.append(staff_id)
        else:
            email = fields.get("Name", "").strip()
            if email:
                city_staff_emails.append(email)

    return delivery_staff_ids, city_staff_emails

# ─── Airtable matching ────────────────────────────────────────────────────────

def _escape(s):
    return s.replace("'", "\\'")

def find_meeting(title, meeting_dt):
    """Find the MEETINGS_TABLE record matching this title. Narrows by date if multiple."""
    # TRIM() handles accidental leading/trailing spaces in the Airtable title field
    formula = f"TRIM({{Title}})='{_escape(title.strip())}'"
    data    = at_get(MEETINGS_TABLE, {"filterByFormula": formula})
    records = data.get("records", [])

    if not records:
        return None
    if len(records) == 1:
        return records[0]

    date_prefix = meeting_dt.strftime("%Y-%m-%d")
    for r in records:
        if r["fields"].get("Start", "").startswith(date_prefix):
            return r

    return records[0]

def find_existing_report(meeting_record_id):
    """
    Return an existing report record linked to this meeting, if any.

    NOTE: Airtable formula fields on linked-record fields return the linked
    record's primary field value (its title), not its ID — so FIND(..., ARRAYJOIN(...))
    doesn't work for ID-based lookups. We fetch recent records and match in Python instead.
    """
    data    = at_get(REPORTS_TABLE, {
        "maxRecords": 100,
        "sort[0][field]": "Record Created Time",
        "sort[0][direction]": "desc",
    })
    for record in data.get("records", []):
        if meeting_record_id in record["fields"].get("Select Event", []):
            return record
    return None

# ─── Processed log ────────────────────────────────────────────────────────────

def load_processed():
    if PROCESSED_LOG.exists():
        return set(json.loads(PROCESSED_LOG.read_text()))
    return set()

def mark_processed(folder_name, processed):
    processed.add(folder_name)
    PROCESSED_LOG.write_text(json.dumps(sorted(processed), indent=2))

# ─── Core: process one meeting group ─────────────────────────────────────────

def process_group(sessions, title):
    """
    sessions : [(datetime, Path), ...] sorted by time — may be 1 or many
    title    : meeting title string (from folder name)
    Returns True on success, False if skipped.
    """
    first_dt = sessions[0][0]

    # 1. Find Airtable meeting record
    meeting = find_meeting(title, first_dt)
    if not meeting:
        print(f"  ⚠️  No Airtable record found for '{title}' — skipping")
        return False
    print(f"  ✓  Matched meeting: {meeting['id']}")

    # 2. Filter sessions to the scheduled window + buffer
    sessions = filter_to_window(sessions, meeting)
    if not sessions:
        print("  ⚠️  No sessions fall within the scheduled meeting window — skipping")
        return False

    multi = len(sessions) > 1

    # 3. Transcribe each session
    parts = []
    for i, (dt, folder) in enumerate(sessions, 1):
        label = f"Session {i}" if multi else ""

        call_text = read_vtt(folder)
        if call_text:
            print(f"  ✓  {'[' + label + '] ' if label else ''}Using existing VTT")
        else:
            audio = find_audio(folder)
            if not audio:
                print(f"  ⚠️  {'[' + label + '] ' if label else ''}No audio — skipping session")
                continue
            call_text = transcribe_audio(audio, session_label=label)

        chat_text = read_chat(folder)
        if chat_text:
            print(f"  ✓  {'[' + label + '] ' if label else ''}Chat transcript found")

        session_block = build_combined(call_text, chat_text)
        if multi:
            session_block = f"SESSION {i} — {dt.strftime('%H:%M')}\n\n{session_block}"
        parts.append(session_block)

    if not parts:
        return False

    combined = "\n\n---\n\n".join(parts)
    print(f"  ✓  Total transcript: {len(combined):,} chars{' across ' + str(len(parts)) + ' sessions' if multi else ''}")

    # 4. Infer event type
    event_type = infer_event_type(title)
    print(f"  ✓  Event type: {event_type}")

    # 5. Resolve attendees
    delivery_staff_ids, city_staff_emails = resolve_attendees(meeting)
    if delivery_staff_ids:
        print(f"  ✓  Delivery staff: {len(delivery_staff_ids)} record(s)")
    if city_staff_emails:
        print(f"  ✓  City staff: {', '.join(city_staff_emails)}")

    # 6. Build Airtable fields
    report_fields = {
        TRANSCRIPT_FIELD: combined,
        "Event Type":     event_type,
    }
    if delivery_staff_ids:
        report_fields["Attendees - Delivery Staff"] = delivery_staff_ids
    if city_staff_emails:
        report_fields[CITY_STAFF_FIELD] = ", ".join(city_staff_emails)

    # 7. Create or update report record
    report = find_existing_report(meeting["id"])
    if report:
        print(f"  ✓  Updating existing report: {report['id']}")
        at_patch(REPORTS_TABLE, report["id"], report_fields)
    else:
        print("  ✓  Creating new report record ...")
        report_fields[SELECT_EVENT_FIELD] = [meeting["id"]]
        new_rec = at_create(REPORTS_TABLE, report_fields)
        print(f"  ✓  Created: {new_rec['id']}")

    return True

# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    if not AIRTABLE_TOKEN:
        print("❌  AIRTABLE_TOKEN not set — check your .env file.")
        sys.exit(1)

    processed = load_processed()

    # Collect unprocessed Zoom folders
    all_folders = sorted(
        [f for f in ZOOM_DIR.iterdir()
         if f.is_dir() and not f.name.startswith(".") and f.name not in processed],
        key=lambda f: f.name
    )

    # Group by (date, title) — handles meetings that end and restart
    groups = group_folders(all_folders)

    if not groups:
        print("✓  No new recordings to process.")
        return

    print(f"Found {len(groups)} meeting group(s)\n")

    for (date, title), sessions in sorted(groups.items()):
        suffix = f" ({len(sessions)} sessions)" if len(sessions) > 1 else ""
        print(f"▶  {date} — {title}{suffix}")
        try:
            success = process_group(sessions, title)
            if success:
                for _, folder in sessions:
                    mark_processed(folder.name, processed)
                print("  ✅  Done\n")
            else:
                print("  ⏭   Skipped\n")
        except Exception as e:
            print(f"  ❌  Error: {e}\n")

    print("All done.")

if __name__ == "__main__":
    main()

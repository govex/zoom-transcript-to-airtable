#!/bin/bash
# run.sh — manually trigger the Zoom → Airtable transcript automation
cd "$(dirname "$0")"
python3 zoom_to_airtable.py

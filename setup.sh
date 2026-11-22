#!/usr/bin/env bash
# One-time setup: ffmpeg check, Python virtualenv in .venv, Python dependencies.
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || sudo apt install -y ffmpeg
python3 -m venv --clear .venv   # --clear: rebuild a stale/moved .venv instead of reusing it
.venv/bin/python -m pip install -q "UnityPy==1.25.4" numpy pillow
echo "ok - try: ./md2switch search <romfs> <text>"
echo "      or: ./md2switch build <romfs> <chart.mdm> <song_id>"

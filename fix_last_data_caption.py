#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old_start = '"DropPrice is the percentage fall from the peak selected by C4.'
start = text.find(old_start)

if start == -1:
    print("No change: old DropPrice/ChangePrice caption block was not found.")
else:
    line_start = text.rfind("\n", 0, start) + 1
    end_marker = '"C5 lookback window."'
    end = text.find(end_marker, start)

    if end == -1:
        print("No change: caption end marker was not found.")
    else:
        end += len(end_marker)
        replacement = '''            "DropDuration is the shortest elapsed period ending at LastCollect during which price "
            "did not exceed LastPrice by more than the configured C4/C5 movement threshold. "
            "StaticDuration is the shortest elapsed period ending at LastCollect during which price "
            "stayed inside the configured +/- movement-threshold band around LastPrice. "
            "WaitToTrade and WaitToOpening show the remaining time to the relevant trading phase."'''
        text = text[:line_start] + replacement + text[end:]
        path.write_text(text, encoding="utf-8")
        print("Updated remaining Last Data caption block.")

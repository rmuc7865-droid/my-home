#!/usr/bin/env python3
from pathlib import Path
import shutil
from datetime import datetime, timezone

path = Path("dashboard/streamlit_app.py")
if not path.exists():
    raise SystemExit("ERROR: dashboard/streamlit_app.py not found. Run from /opt/home-monitor.")

text = path.read_text(encoding="utf-8")
original = text

start = text.find('elif page == "Last Data":')
end = text.find('elif page == "Alerts":', start)
if start == -1 or end == -1:
    raise SystemExit("ERROR: Last Data page boundaries not found.")

section = text[start:end]

caption_marker = '''        st.caption(
            "Last Data shows one row for every ticker, regardless of its current BUY/SELL "
'''

if caption_marker not in section:
    raise SystemExit("ERROR: Last Data main caption marker not found.")

steps = '''        st.subheader("Steps to analyse tickers")
        st.markdown(
            "1. Is **Close2h** very high or very low, is **DropDur2%** short, "
            "or is **ChangeDur2%** long?\\n"
            "2. Is now the trading time?\\n"
            "3. Is **LastSelling** short?\\n"
            "4. If all are yes, review this ticker on the **Zero-Trading** page."
        )

'''

if 'st.subheader("Steps to analyse tickers")' not in section:
    section = section.replace(caption_marker, steps + caption_marker, 1)

section = section.replace(
    '"CloseB from highest to lowest. LastCollect is the timestamp of the latest market "',
    '"Close2h from highest to lowest. LastCollect is the timestamp of the latest market "',
    1,
)

section = section.replace(
    '"its estimated EUR price. Records counts unique market bars from 03:00 Europe/Berlin, "',
    '"its estimated EUR price. DayRecs counts unique market bars from 03:00 Europe/Berlin, "',
    1,
)

text = text[:start] + section + text[end:]

if text == original:
    raise SystemExit("INFO: requested final Last Data presentation changes already appear applied.")

compile(text, str(path), "exec")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(f"{path.name}.bak-before-last-data-final-{stamp}")
shutil.copy2(path, backup)
path.write_text(text, encoding="utf-8")

print("SUCCESS: final Last Data presentation changes applied.")
print(f"Backup: {backup}")
print("Added: Steps to analyse tickers")
print("Caption: CloseB -> Close2h")
print("Caption: Records -> DayRecs")

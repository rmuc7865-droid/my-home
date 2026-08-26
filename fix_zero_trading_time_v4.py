#!/usr/bin/env python3
from pathlib import Path
import re
import shutil
from datetime import datetime, timezone

path = Path("dashboard/streamlit_app.py")
if not path.exists():
    raise SystemExit("ERROR: dashboard/streamlit_app.py not found. Run from /opt/home-monitor.")

text = path.read_text(encoding="utf-8")
original = text

start = text.find('if page == "Zero-Trading":')
end = text.find('elif page == "Last Data":', start)
if start == -1 or end == -1:
    raise SystemExit("ERROR: Zero-Trading page boundaries not found.")

section = text[start:end]

# 1) LastCollect: same clock value as Last Data; no extra timezone conversion.
helper_start = section.find("def _zero_local_timestamp")
if helper_start == -1:
    raise SystemExit("ERROR: _zero_local_timestamp helper not found.")

helper_line_start = section.rfind("\n", 0, helper_start) + 1
helper_end = section.find("\n                def ", helper_start + 5)
if helper_end == -1:
    helper_end = min(len(section), helper_start + 1500)

indent = section[helper_line_start:helper_start]

replacement_helper = (
    indent + "def _zero_local_timestamp(value):\n"
    + indent + "    parsed = pd.to_datetime(\n"
    + indent + "        value,\n"
    + indent + "        errors=\"coerce\",\n"
    + indent + "    )\n"
    + indent + "    if pd.isna(parsed):\n"
    + indent + "        return \"—\"\n"
    + indent + "    return parsed.strftime(\"%H:%M\")\n"
)

section = section[:helper_line_start] + replacement_helper + section[helper_end:]

# 2) WaitToTrade / WaitToOpening:
#    HH:mm when days == 0; Xd HH:mm when days > 0.
wait_start = section.find("def _zero_timedelta_text")
if wait_start == -1:
    raise SystemExit("ERROR: _zero_timedelta_text helper not found.")

wait_end = section.find("\n                def ", wait_start + 5)
if wait_end == -1:
    wait_end = min(len(section), wait_start + 2600)

wait_helper = section[wait_start:wait_end]

patterns = [
    r'return\s+f"\{days\}d \{hours:02d\}:\{minutes:02d\}"',
    r'return\s*\(\s*f"\{days\}d "\s*f"\{hours:02d\}:\{minutes:02d\}"\s*\)',
]

new_return = (
    'return (\n'
    '                        f"{days}d {hours:02d}:{minutes:02d}"\n'
    '                        if days > 0\n'
    '                        else f"{hours:02d}:{minutes:02d}"\n'
    '                    )'
)

changed = False
for pat in patterns:
    wait_helper_new, n = re.subn(
        pat,
        new_return,
        wait_helper,
        count=1,
        flags=re.S,
    )
    if n:
        wait_helper = wait_helper_new
        changed = True
        break

if not changed:
    if (
        "if days > 0" not in wait_helper
        or 'else f"{hours:02d}:{minutes:02d}"' not in wait_helper
    ):
        raise SystemExit("ERROR: Wait duration return format not recognized.")

section = section[:wait_start] + wait_helper + section[wait_end:]

# 3) Notes.
section = section.replace(
    "LastCollect is shown as local HH:mm.",
    "LastCollect uses the same market-bar HH:mm value as Last Data.",
)
section = section.replace(
    "LastCollect is shown in local time as HH:mm.",
    "LastCollect uses the same market-bar HH:mm value as Last Data.",
)
section = section.replace(
    "are displayed as Xd HH:mm.",
    "are displayed as HH:mm, or Xd HH:mm when one or more full days remain.",
)

text = text[:start] + section + text[end:]

if text == original:
    raise SystemExit("INFO: requested corrections already appear to be applied.")

compile(text, str(path), "exec")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(f"{path.name}.bak-before-zero-trading-time-v4-{stamp}")
shutil.copy2(path, backup)
path.write_text(text, encoding="utf-8")

print("SUCCESS: Zero-Trading time formatting corrected.")
print(f"Backup: {backup}")
print("LastCollect: same HH:mm market-bar value as Last Data.")
print("Wait values: HH:mm when days=0; Xd HH:mm when days>0.")

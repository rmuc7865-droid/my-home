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

# ------------------------------------------------------------
# 1) Remove portfolio counter metrics robustly.
# Keep zero_open_count / zero_max_open / zero_available_open_slots
# because they are still used to enforce/display the portfolio warning.
# ------------------------------------------------------------
patterns = [
    r'(?ms)^\s*portfolio_cols\s*=\s*st\.columns\(3\)\s*\n'
    r'.*?portfolio_cols\[2\]\.metric\(.*?\)\s*\n',
    r'(?ms)^\s*portfolio_cols\s*=\s*st\.columns\([^\n]+\)\s*\n'
    r'.*?"OPEN tickers".*?"Maximum OPEN".*?"Available BUY slots".*?\n(?=\s*if zero_available_open_slots)',
]

removed = False
for pat in patterns:
    new_section, n = re.subn(pat, '', section, count=1)
    if n:
        section = new_section
        removed = True
        break

if not removed:
    # If counters were already removed, do not fail.
    if (
        '"OPEN tickers"' in section
        or '"Maximum OPEN"' in section
        or '"Available BUY slots"' in section
    ):
        raise SystemExit(
            "ERROR: portfolio counter labels still exist but their block format was not recognized."
        )

# ------------------------------------------------------------
# 2) LastCollect -> HH:mm local time only.
# Replace only the Zero-Trading formatter.
# ------------------------------------------------------------
section, n = re.subn(
    r'(def _zero_local_timestamp\(value\):.*?\.strftime\()'
    r'["\']%Y-%m-%d %H:%M["\']'
    r'(\))',
    r'\1"%H:%M"\2',
    section,
    count=1,
    flags=re.S,
)
if n == 0 and 'def _zero_local_timestamp' not in section:
    raise SystemExit("ERROR: Zero-Trading local timestamp formatter not found.")

# ------------------------------------------------------------
# 3) Wait values -> explicit Xd HH:mm.
# Existing formatter may be multiline or single line.
# ------------------------------------------------------------
if 'def _zero_timedelta_text' not in section:
    raise SystemExit("ERROR: Zero-Trading timedelta formatter not found.")

section = section.replace(
    '''                    return (
                        f"{days}d "
                        f"{hours:02d}:{minutes:02d}"
                    )
''',
    '''                    return f"{days}d {hours:02d}:{minutes:02d}"
''',
    1,
)

# ------------------------------------------------------------
# 4) Column order: LastCollect before WaitToTrade.
# ------------------------------------------------------------
old_order = '''                    "TickerName",
                    "WaitToTrade",
                    "WaitToOpening",
                    "Qty",
                    "InitTimeLatest",
                    "LastCollect",
'''
new_order = '''                    "TickerName",
                    "LastCollect",
                    "WaitToTrade",
                    "WaitToOpening",
                    "Qty",
                    "InitTimeLatest",
'''

if old_order in section:
    section = section.replace(old_order, new_order, 1)
else:
    # Already corrected is acceptable.
    corrected_fragment = '''                    "TickerName",
                    "LastCollect",
                    "WaitToTrade",
                    "WaitToOpening",
'''
    if corrected_fragment not in section:
        raise SystemExit("ERROR: Zero-Trading requested_columns order not recognized.")

# ------------------------------------------------------------
# 5) Correct sell step 3 wording.
# ------------------------------------------------------------
section = section.replace(
    'price change smaller than 2%',
    'price change larger than 2%',
)
section = section.replace(
    'or is the relevant price change condition satisfied?',
    'or is the ticker price change larger than 2%?',
)

# ------------------------------------------------------------
# 6) Update note for LastCollect formatting if present.
# ------------------------------------------------------------
section = section.replace(
    '''"LastClose2h from highest to lowest. WaitToTrade and "
            "WaitToOpening use the same Newest-data market-phase calculation "
            "as Last Data and are displayed as Xd HH:mm."''',
    '''"LastClose2h from highest to lowest. LastCollect is shown in local "
            "time as HH:mm. WaitToTrade and WaitToOpening use the same "
            "Newest-data market-phase calculation as Last Data and are displayed "
            "as Xd HH:mm."''',
)

text = text[:start] + section + text[end:]

if text == original:
    raise SystemExit("INFO: requested Zero-Trading corrections already appear to be applied.")

# Syntax-check before write.
compile(text, str(path), "exec")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(f"{path.name}.bak-before-zero-trading-v3b-{stamp}")
shutil.copy2(path, backup)
path.write_text(text, encoding="utf-8")

print("SUCCESS: Zero-Trading corrections applied.")
print(f"Backup: {backup}")
print("Counters removed (portfolio enforcement/warning retained).")
print("LastCollect moved before WaitToTrade and formatted HH:mm.")
print("WaitToTrade / WaitToOpening kept in Xd HH:mm format.")
print("Sell step 3 now uses price change larger than 2%.")

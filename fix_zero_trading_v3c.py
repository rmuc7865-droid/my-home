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
# 1) Remove only the three displayed portfolio metrics.
# Keep the variables and warning because they enforce the BUY cap.
# ------------------------------------------------------------
metric_labels = ("OPEN tickers", "Maximum OPEN", "Available BUY slots")

if any(f'"{label}"' in section for label in metric_labels):
    # Remove the portfolio_cols declaration if present.
    section = re.sub(
        r'(?m)^[ \t]*portfolio_cols[ \t]*=[ \t]*st\.columns\([^\n]*\)\n',
        '',
        section,
        count=1,
    )

    # Remove each portfolio_cols[n].metric(...) call using balanced-paren scanning.
    for label in metric_labels:
        label_pos = section.find(f'"{label}"')
        if label_pos == -1:
            continue

        call_start = section.rfind("portfolio_cols[", 0, label_pos)
        if call_start == -1:
            raise SystemExit(f'ERROR: cannot locate metric call for "{label}".')

        # Extend to start of line.
        line_start = section.rfind("\n", 0, call_start) + 1

        paren_start = section.find("(", call_start)
        if paren_start == -1:
            raise SystemExit(f'ERROR: malformed metric call for "{label}".')

        depth = 0
        quote = None
        escape = False
        call_end = None

        for i in range(paren_start, len(section)):
            ch = section[i]

            if quote is not None:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    quote = None
                continue

            if ch in ("'", '"'):
                quote = ch
                continue

            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    call_end = i + 1
                    break

        if call_end is None:
            raise SystemExit(f'ERROR: cannot parse metric call for "{label}".')

        # Consume trailing whitespace/newline.
        while call_end < len(section) and section[call_end] in " \t":
            call_end += 1
        if call_end < len(section) and section[call_end] == "\n":
            call_end += 1

        section = section[:line_start] + section[call_end:]

# ------------------------------------------------------------
# 2) Rewrite requested_columns structurally.
# ------------------------------------------------------------
assignment = re.search(
    r'(?m)^(?P<indent>[ \t]*)requested_columns[ \t]*=[ \t]*\[',
    section,
)
if not assignment:
    raise SystemExit("ERROR: Zero-Trading requested_columns assignment not found.")

list_start = assignment.end() - 1  # points to '['
depth = 0
quote = None
escape = False
list_end = None

for i in range(list_start, len(section)):
    ch = section[i]

    if quote is not None:
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == quote:
            quote = None
        continue

    if ch in ("'", '"'):
        quote = ch
        continue

    if ch == "[":
        depth += 1
    elif ch == "]":
        depth -= 1
        if depth == 0:
            list_end = i + 1
            break

if list_end is None:
    raise SystemExit("ERROR: could not parse requested_columns list.")

indent = assignment.group("indent")
item_indent = indent + "    "

columns = [
    "Action",
    "Ticker",
    "TickerName",
    "LastCollect",
    "WaitToTrade",
    "WaitToOpening",
    "Qty",
    "InitTimeLatest",
    "LastSelling",
    "LastTops",
    "LastClose2h",
    "Drop24h",
    "DropInitTimeLatest",
    "Change24h",
    "ChangeInitTimeLatest",
]

replacement = "[\n" + "".join(
    f'{item_indent}"{column}",\n'
    for column in columns
) + indent + "]"

section = section[:list_start] + replacement + section[list_end:]

# ------------------------------------------------------------
# 3) LastCollect display: HH:mm in local timezone.
# Find the Zero-Trading helper and replace only its strftime format.
# ------------------------------------------------------------
helper_start = section.find("def _zero_local_timestamp")
if helper_start == -1:
    raise SystemExit("ERROR: _zero_local_timestamp helper not found.")

helper_end = section.find("\n                def ", helper_start + 5)
if helper_end == -1:
    helper_end = min(len(section), helper_start + 1200)

helper = section[helper_start:helper_end]

new_helper, n = re.subn(
    r'\.strftime\(\s*["\']%Y-%m-%d %H:%M["\']\s*\)',
    '.strftime("%H:%M")',
    helper,
    count=1,
)

if n == 0:
    # Already corrected is acceptable.
    if '.strftime("%H:%M")' not in helper and ".strftime('%H:%M')" not in helper:
        raise SystemExit("ERROR: LastCollect timestamp format in helper not recognized.")

section = section[:helper_start] + new_helper + section[helper_end:]

# ------------------------------------------------------------
# 4) Wait formatter: always Xd HH:mm.
# Replace return expression inside _zero_timedelta_text.
# ------------------------------------------------------------
wait_start = section.find("def _zero_timedelta_text")
if wait_start == -1:
    raise SystemExit("ERROR: _zero_timedelta_text helper not found.")

wait_end = section.find("\n                def ", wait_start + 5)
if wait_end == -1:
    wait_end = min(len(section), wait_start + 2200)

wait_helper = section[wait_start:wait_end]

# Handle multiline and single-line variants.
wait_helper_new = re.sub(
    r'return\s*\(\s*f"\{days\}d\s*"\s*f"\{hours:02d\}:\{minutes:02d\}"\s*\)',
    'return f"{days}d {hours:02d}:{minutes:02d}"',
    wait_helper,
    count=1,
    flags=re.S,
)
wait_helper_new = wait_helper_new.replace(
    'return f"{days}d {hours:02d}:{minutes:02d}"',
    'return f"{days}d {hours:02d}:{minutes:02d}"',
    1,
)

if 'f"{days}d {hours:02d}:{minutes:02d}"' not in wait_helper_new:
    raise SystemExit("ERROR: WaitToTrade/WaitToOpening formatter not recognized.")

section = section[:wait_start] + wait_helper_new + section[wait_end:]

# ------------------------------------------------------------
# 5) Correct Steps to sell a ticker, step 3.
# ------------------------------------------------------------
section = section.replace(
    "price change smaller than 2%",
    "price change larger than 2%",
)
section = section.replace(
    "or is the relevant price change condition satisfied?",
    "or is the ticker price change larger than 2%?",
)

# ------------------------------------------------------------
# 6) Notes: describe current display.
# ------------------------------------------------------------
section = section.replace(
    "LastClose2h from highest to lowest. WaitToTrade and "
    '"WaitToOpening use the same Newest-data market-phase calculation "',
    "LastClose2h from highest to lowest. LastCollect is shown as local HH:mm. "
    '"WaitToTrade and WaitToOpening use the same Newest-data market-phase calculation "',
)

text = text[:start] + section + text[end:]

if text == original:
    raise SystemExit("INFO: no changes needed; requested corrections already appear present.")

# Complete syntax check before write.
compile(text, str(path), "exec")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(f"{path.name}.bak-before-zero-trading-v3c-{stamp}")
shutil.copy2(path, backup)
path.write_text(text, encoding="utf-8")

print("SUCCESS: Zero-Trading corrections applied.")
print(f"Backup: {backup}")
print("Counters removed; portfolio limit warning/enforcement retained.")
print("Column order now starts: Action, Ticker, TickerName, LastCollect, WaitToTrade, WaitToOpening.")
print("LastCollect format: local HH:mm.")
print("WaitToTrade / WaitToOpening format: Xd HH:mm.")
print("Sell step 3: price drop >2% OR price change >2%.")

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

# Replace the complete Zero-Trading duration helper.
helper_start = section.find("def _zero_timedelta_text")
if helper_start == -1:
    raise SystemExit("ERROR: _zero_timedelta_text helper not found.")

helper_line_start = section.rfind("\n", 0, helper_start) + 1
next_def = section.find("\n                def ", helper_start + 5)
if next_def == -1:
    raise SystemExit("ERROR: could not find end of _zero_timedelta_text helper.")

indent = section[helper_line_start:helper_start]

helper_lines = [
    indent + "def _zero_timedelta_text(value):",
    indent + "    if value is None:",
    indent + "        return \"—\"",
    "",
    indent + "    total_minutes = None",
    "",
    indent + "    if isinstance(value, str):",
    indent + "        raw = value.strip()",
    indent + "        if not raw or raw in {\"-\", \"—\", \"None\", \"nan\", \"NaT\"}:",
    indent + "            return \"—\"",
    "",
    indent + "        match = re.fullmatch(r\"(\\d+):(\\d{2})\", raw)",
    indent + "        if match:",
    indent + "            hours_total = int(match.group(1))",
    indent + "            minutes_part = int(match.group(2))",
    indent + "            if minutes_part >= 60:",
    indent + "                return raw",
    indent + "            total_minutes = hours_total * 60 + minutes_part",
    indent + "        else:",
    indent + "            try:",
    indent + "                delta = pd.to_timedelta(raw)",
    indent + "            except Exception:",
    indent + "                return raw",
    indent + "            if pd.isna(delta):",
    indent + "                return \"—\"",
    indent + "            total_minutes = max(0, int(round(delta.total_seconds() / 60.0)))",
    indent + "    else:",
    indent + "        try:",
    indent + "            delta = pd.to_timedelta(value)",
    indent + "        except Exception:",
    indent + "            return \"—\"",
    indent + "        if pd.isna(delta):",
    indent + "            return \"—\"",
    indent + "        total_minutes = max(0, int(round(delta.total_seconds() / 60.0)))",
    "",
    indent + "    days, remainder = divmod(total_minutes, 24 * 60)",
    indent + "    hours, minutes = divmod(remainder, 60)",
    indent + "    if days > 0:",
    indent + "        return f\"{days}d {hours:02d}:{minutes:02d}\"",
    indent + "    return f\"{hours:02d}:{minutes:02d}\"",
    "",
]
new_helper = "\n".join(helper_lines)

section = section[:helper_line_start] + new_helper + section[next_def:]

# Add counters before the table if not already present.
if '"Buy assets"' not in section:
    anchor = "                st.dataframe(\n                    styled_display,"
    pos = section.find(anchor)
    if pos == -1:
        raise SystemExit("ERROR: styled Zero-Trading dataframe anchor not found.")

    counters = '''                buy_asset_count = int(
                    (display["Action"] == "Buy").sum()
                )
                sell_asset_count = int(
                    (display["Action"] == "Sell").sum()
                )

                newest_display = pd.to_datetime(
                    newest_market_data,
                    errors="coerce",
                )
                newest_display = (
                    newest_display.strftime("%H:%M")
                    if pd.notna(newest_display)
                    else "—"
                )

                counter_cols = st.columns(3)
                counter_cols[0].metric("Newest data", newest_display)
                counter_cols[1].metric("Buy assets", buy_asset_count)
                counter_cols[2].metric("Sell assets", sell_asset_count)

'''
    section = section[:pos] + counters + section[pos:]

# Update explanatory note if present.
section = section.replace(
    "are displayed as HH:mm, or Xd HH:mm when one or more full days remain.",
    "are displayed as HH:mm, or Xd HH:mm when one or more full days remain "
    "(for example, 61:45 is displayed as 2d 13:45).",
)

text = text[:start] + section + text[end:]

if text == original:
    raise SystemExit("INFO: no changes needed.")

compile(text, str(path), "exec")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(f"{path.name}.bak-before-zero-trading-counters-v5-{stamp}")
shutil.copy2(path, backup)
path.write_text(text, encoding="utf-8")

print("SUCCESS: Zero-Trading counters and wait formatting updated.")
print(f"Backup: {backup}")
print("Counters added: Newest data, Buy assets, Sell assets.")
print("Wait format: HH:mm for <24h; Xd HH:mm for >=24h.")
print("Example: 61:45 -> 2d 13:45.")

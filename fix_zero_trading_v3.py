#!/usr/bin/env python3
from pathlib import Path
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

old = '''            portfolio_cols = st.columns(3)
            portfolio_cols[0].metric(
                "OPEN tickers",
                zero_open_count,
            )
            portfolio_cols[1].metric(
                "Maximum OPEN",
                zero_max_open,
            )
            portfolio_cols[2].metric(
                "Available BUY slots",
                zero_available_open_slots,
            )

'''
if old not in section:
    raise SystemExit("ERROR: Zero-Trading portfolio counter block not found.")
section = section.replace(old, "", 1)

old = '''                def _zero_local_timestamp(value):
                    parsed = pd.to_datetime(
                        value,
                        utc=True,
                        errors="coerce",
                    )
                    if pd.isna(parsed):
                        return "—"
                    return parsed.tz_convert(
                        LOCAL_TIMEZONE
                    ).strftime("%Y-%m-%d %H:%M")
'''
new = '''                def _zero_local_timestamp(value):
                    parsed = pd.to_datetime(
                        value,
                        utc=True,
                        errors="coerce",
                    )
                    if pd.isna(parsed):
                        return "—"
                    return parsed.tz_convert(
                        LOCAL_TIMEZONE
                    ).strftime("%H:%M")
'''
if old not in section:
    raise SystemExit("ERROR: Zero-Trading local timestamp formatter not found.")
section = section.replace(old, new, 1)

old = '''                    return (
                        f"{days}d "
                        f"{hours:02d}:{minutes:02d}"
                    )
'''
new = '''                    return f"{days}d {hours:02d}:{minutes:02d}"
'''
if old not in section:
    raise SystemExit("ERROR: Zero-Trading timedelta formatter not found.")
section = section.replace(old, new, 1)

old = '''                requested_columns = [
                    "Action",
                    "Ticker",
                    "TickerName",
                    "WaitToTrade",
                    "WaitToOpening",
                    "Qty",
                    "InitTimeLatest",
                    "LastCollect",
                    "LastSelling",
'''
new = '''                requested_columns = [
                    "Action",
                    "Ticker",
                    "TickerName",
                    "LastCollect",
                    "WaitToTrade",
                    "WaitToOpening",
                    "Qty",
                    "InitTimeLatest",
                    "LastSelling",
'''
if old not in section:
    raise SystemExit("ERROR: Zero-Trading requested column order block not found.")
section = section.replace(old, new, 1)

old = '''            "3. Is the price drop larger than 2%, or is the relevant price "
            "change condition satisfied?\\n"
'''
new = '''            "3. Is the ticker price drop larger than 2%, or is the ticker "
            "price change larger than 2%?\\n"
'''
if old in section:
    section = section.replace(old, new, 1)
else:
    old2 = '''            "3. Is the ticker price drop larger than 2%, or is the ticker price "
            "change smaller than 2%?\\n"
'''
    if old2 not in section:
        raise SystemExit("ERROR: Zero-Trading sell step 3 text not found.")
    section = section.replace(old2, new, 1)

old_note = '''            "Rows are sorted first by Action (Buy before Sell), then by "
            "LastClose2h from highest to lowest. WaitToTrade and "
            "WaitToOpening use the same Newest-data market-phase calculation "
            "as Last Data and are displayed as Xd HH:mm."
'''
new_note = '''            "Rows are sorted first by Action (Buy before Sell), then by "
            "LastClose2h from highest to lowest. LastCollect is shown in local "
            "time as HH:mm. WaitToTrade and WaitToOpening use the same "
            "Newest-data market-phase calculation as Last Data and are displayed "
            "as Xd HH:mm."
'''
if old_note in section:
    section = section.replace(old_note, new_note, 1)

text = text[:start] + section + text[end:]

if text == original:
    raise SystemExit("ERROR: no changes generated.")

compile(text, str(path), "exec")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(f"{path.name}.bak-before-zero-trading-v3-{stamp}")
shutil.copy2(path, backup)
path.write_text(text, encoding="utf-8")

print("SUCCESS: Zero-Trading corrections applied.")
print(f"Backup: {backup}")
print("Removed portfolio counters.")
print("LastCollect moved before WaitToTrade and formatted as HH:mm.")
print("WaitToTrade / WaitToOpening formatted as Xd HH:mm.")
print("Sell step 3 now says price change larger than 2%.")

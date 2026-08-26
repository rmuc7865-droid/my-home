#!/usr/bin/env python3
from pathlib import Path
import shutil
from datetime import datetime, timezone

path = Path("dashboard/streamlit_app.py")
if not path.exists():
    raise SystemExit("ERROR: dashboard/streamlit_app.py not found. Run from /opt/home-monitor.")

text = path.read_text(encoding="utf-8")

old_helper = '''                def _zero_timedelta_text(value):
                    if value is None:
                        return "—"
                    if isinstance(value, str):
                        raw = value.strip()
                        if not raw or raw in {"-", "—", "None", "nan", "NaT"}:
                            return "—"
                        try:
                            delta = pd.to_timedelta(raw)
                        except Exception:
                            return raw
                    else:
                        try:
                            delta = pd.to_timedelta(value)
                        except Exception:
                            return "—"
                    if pd.isna(delta):
                        return "—"
                    total_minutes = max(0, int(round(delta.total_seconds() / 60.0)))
                    days, remainder = divmod(total_minutes, 24 * 60)
                    hours, minutes = divmod(remainder, 60)
                    return (
                        f"{days}d {hours:02d}:{minutes:02d}"
                        if days > 0
                        else f"{hours:02d}:{minutes:02d}"
                    )
'''

new_helper = '''                def _zero_timedelta_text(value):
                    if value is None:
                        return "—"

                    total_minutes = None

                    if isinstance(value, str):
                        raw = value.strip()
                        if not raw or raw in {"-", "—", "None", "nan", "NaT"}:
                            return "—"

                        # Last Data may supply wait values as total-hours HH:MM,
                        # including values above 24 hours such as 61:45.
                        parts = raw.split(":")
                        if (
                            len(parts) == 2
                            and parts[0].isdigit()
                            and parts[1].isdigit()
                        ):
                            total_hours = int(parts[0])
                            minute_part = int(parts[1])
                            if 0 <= minute_part < 60:
                                total_minutes = total_hours * 60 + minute_part

                        if total_minutes is None:
                            try:
                                delta = pd.to_timedelta(raw)
                            except Exception:
                                return raw
                            if pd.isna(delta):
                                return "—"
                            total_minutes = max(
                                0,
                                int(round(delta.total_seconds() / 60.0)),
                            )
                    else:
                        try:
                            delta = pd.to_timedelta(value)
                        except Exception:
                            return "—"
                        if pd.isna(delta):
                            return "—"
                        total_minutes = max(
                            0,
                            int(round(delta.total_seconds() / 60.0)),
                        )

                    days, remainder = divmod(total_minutes, 24 * 60)
                    hours, minutes = divmod(remainder, 60)

                    return (
                        f"{days}d {hours:02d}:{minutes:02d}"
                        if days > 0
                        else f"{hours:02d}:{minutes:02d}"
                    )
'''

if old_helper not in text:
    raise SystemExit(
        "ERROR: exact current _zero_timedelta_text block not found; no changes written."
    )

text = text.replace(old_helper, new_helper, 1)

old_table = '''                styled_display = display.style.apply(_zero_row_style, axis=1)
                st.dataframe(styled_display, use_container_width=True, hide_index=True)
'''

new_table = '''                styled_display = display.style.apply(_zero_row_style, axis=1)

                buy_asset_count = int(
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
                counter_cols[0].metric(
                    "Newest data",
                    newest_display,
                )
                counter_cols[1].metric(
                    "Buy assets",
                    buy_asset_count,
                )
                counter_cols[2].metric(
                    "Sell assets",
                    sell_asset_count,
                )

                st.dataframe(
                    styled_display,
                    use_container_width=True,
                    hide_index=True,
                )
'''

if old_table not in text:
    raise SystemExit(
        "ERROR: exact current Zero-Trading dataframe block not found; no changes written."
    )

text = text.replace(old_table, new_table, 1)

old_note = '''            "Action (Buy before Sell), then by LastClose2h from highest to lowest. WaitToTrade and "
            "WaitToOpening use the same Newest-data market-phase calculation as Last Data and are "
            "displayed as Xd HH:mm."
'''
new_note = '''            "Action (Buy before Sell), then by LastClose2h from highest to lowest. WaitToTrade and "
            "WaitToOpening use the same Newest-data market-phase calculation as Last Data and are "
            "displayed as HH:mm, or Xd HH:mm when one or more full days remain."
'''

if old_note in text:
    text = text.replace(old_note, new_note, 1)

compile(text, str(path), "exec")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(
    f"{path.name}.bak-before-zero-trading-counters-v6-{stamp}"
)
shutil.copy2(path, backup)
path.write_text(text, encoding="utf-8")

print("SUCCESS: Zero-Trading counters and WaitToOpening formatting updated.")
print(f"Backup: {backup}")
print("Counters: Newest data / Buy assets / Sell assets")
print("Duration example: 61:45 -> 2d 13:45")

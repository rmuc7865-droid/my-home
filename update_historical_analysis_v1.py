#!/usr/bin/env python3
from pathlib import Path
import shutil
from datetime import datetime, timezone

path = Path("dashboard/streamlit_app.py")
if not path.exists():
    raise SystemExit("ERROR: dashboard/streamlit_app.py not found. Run from /opt/home-monitor.")

text = path.read_text(encoding="utf-8")

# 1) Sim-Trading note
old = '''            st.subheader("Steps to analyse the simulator results")
            st.markdown(
                "1. Is **DiffLastPrice** very high or very low, or is "
                "**DiffLastTime** very long?\\n"
                "2. If yes, review the ticker in the **Historical Data** and "
                "**Last Data** pages to understand why the simulator has not "
                "sold the ticker."
            )
'''
new = '''            st.subheader("Steps to analyse the simulator results")
            st.markdown(
                "1. For **OPEN** tickers, is **DiffLastPrice** very high or very low, "
                "or is **DiffLastTime** very long?\\n"
                "2. If yes, review the ticker in the **Historical Data** and "
                "**Last Data** pages to understand why the simulator has not "
                "sold the ticker."
            )
'''
if old not in text:
    raise SystemExit("ERROR: Sim-Trading analysis note not found.")
text = text.replace(old, new, 1)

# 2) Historical metadata maps
old = '''                    historical_trade_intervals = []
                    reentry_buy_points = set()

                    try:
'''
new = '''                    historical_trade_intervals = []
                    reentry_buy_points = set()
                    historical_buy_points = {}
                    historical_sell_points = {}

                    try:
'''
if old not in text:
    raise SystemExit("ERROR: historical interval init not found.")
text = text.replace(old, new, 1)

old = '''                                    historical_trade_intervals.append(
                                        (
                                            ticker,
                                            buy_time,
                                            sell_time,
                                        )
                                    )

                                    if pd.notna(previous_sell):
'''
new = '''                                    historical_trade_intervals.append(
                                        (
                                            ticker,
                                            buy_time,
                                            sell_time,
                                        )
                                    )

                                    buy_point = pd.Timestamp(buy_time).round("15min")
                                    historical_buy_points[(ticker, buy_point)] = {
                                        "buy_time": buy_time,
                                        "sell_time": sell_time,
                                    }

                                    if pd.notna(sell_time):
                                        sell_open_point = (
                                            pd.Timestamp(sell_time).round("15min")
                                            - pd.Timedelta(minutes=15)
                                        )
                                        historical_sell_points[
                                            (ticker, sell_open_point)
                                        ] = {
                                            "buy_time": buy_time,
                                            "sell_time": sell_time,
                                            "sell_reason": str(
                                                trade.get("SellReason") or ""
                                            ).strip().upper(),
                                        }

                                    if pd.notna(previous_sell):
'''
if old not in text:
    raise SystemExit("ERROR: historical interval append block not found.")
text = text.replace(old, new, 1)

# 3) Add action/reason helpers before _IsReentry
anchor = '''                        open_points[
                            "_IsReentry"
                        ] = open_points.apply(
'''
if anchor not in text:
    raise SystemExit("ERROR: _IsReentry anchor not found.")

insert = '''                        def _historical_close2h_reason(ticker, point_time):
                            source = historical[
                                historical["ticker"].astype(str).str.upper().eq(
                                    str(ticker).upper()
                                )
                            ].copy()
                            if source.empty:
                                return "Close2h = —"

                            source["timestamp"] = pd.to_datetime(
                                source["timestamp"], utc=True, errors="coerce"
                            )
                            source["_CloseNumeric"] = pd.to_numeric(
                                source["close"], errors="coerce"
                            )
                            source = source[
                                source["timestamp"].notna()
                                & source["_CloseNumeric"].notna()
                                & (source["_CloseNumeric"] > 0)
                                & (source["timestamp"] <= point_time)
                            ].sort_values("timestamp")

                            if source.empty:
                                return "Close2h = —"

                            latest_price = float(source.iloc[-1]["_CloseNumeric"])
                            target = point_time - pd.Timedelta(
                                hours=float(BUY_CONFIG.get("baseline_hours", 2))
                            )
                            tolerance = pd.Timedelta(
                                minutes=int(
                                    BUY_CONFIG.get("baseline_tolerance_minutes", 30)
                                )
                            )
                            candidates = source[
                                (source["timestamp"] - target).abs() <= tolerance
                            ].copy()
                            if candidates.empty:
                                return "Close2h = —"

                            candidates["_Distance"] = (
                                candidates["timestamp"] - target
                            ).abs()
                            baseline_price = float(
                                candidates.sort_values(
                                    ["_Distance", "timestamp"]
                                ).iloc[0]["_CloseNumeric"]
                            )
                            if baseline_price <= 0:
                                return "Close2h = —"

                            close2h = (
                                latest_price / baseline_price - 1.0
                            ) * 100.0
                            return f"Close2h = {close2h:.2f}%"

                        def _historical_sell_reason(
                            ticker,
                            buy_time,
                            point_time,
                            sell_reason,
                        ):
                            source = historical[
                                historical["ticker"].astype(str).str.upper().eq(
                                    str(ticker).upper()
                                )
                            ].copy()
                            if source.empty:
                                return "—"

                            source["timestamp"] = pd.to_datetime(
                                source["timestamp"], utc=True, errors="coerce"
                            )
                            source["_CloseNumeric"] = pd.to_numeric(
                                source["close"], errors="coerce"
                            )
                            source = source[
                                source["timestamp"].notna()
                                & source["_CloseNumeric"].notna()
                                & (source["_CloseNumeric"] > 0)
                                & (source["timestamp"] >= buy_time)
                                & (source["timestamp"] <= point_time)
                            ].sort_values("timestamp")

                            if source.empty:
                                return "—"

                            last_price = float(source.iloc[-1]["_CloseNumeric"])
                            peak_price = float(source["_CloseNumeric"].max())
                            drop_value = (
                                ((peak_price - last_price) / peak_price) * 100.0
                                if peak_price > 0
                                else float("nan")
                            )
                            change_value = float(
                                (((source["_CloseNumeric"] / last_price) - 1.0)
                                 .abs().max()) * 100.0
                            )

                            reason = str(sell_reason or "").upper()
                            if "C4" in reason:
                                return f"DropInitTimeLatest = {drop_value:.2f}%"
                            if "C5" in reason:
                                return f"ChangeInitTimeLatest = {change_value:.2f}%"

                            if drop_value >= change_value:
                                return f"DropInitTimeLatest = {drop_value:.2f}%"
                            return f"ChangeInitTimeLatest = {change_value:.2f}%"

                        def _historical_open_action(row):
                            key = (
                                str(row["ticker"]).upper(),
                                row["_RoundedTime"],
                            )
                            if key in historical_sell_points:
                                return "Sell"
                            if key in historical_buy_points:
                                return "Buy"
                            return "-"

                        def _historical_open_reason(row):
                            key = (
                                str(row["ticker"]).upper(),
                                row["_RoundedTime"],
                            )
                            if key in historical_sell_points:
                                meta = historical_sell_points[key]
                                return _historical_sell_reason(
                                    row["ticker"],
                                    meta["buy_time"],
                                    row["timestamp"],
                                    meta["sell_reason"],
                                )
                            if key in historical_buy_points:
                                return _historical_close2h_reason(
                                    row["ticker"],
                                    row["timestamp"],
                                )
                            return "-"

                        open_points["_Action"] = open_points.apply(
                            _historical_open_action, axis=1
                        )
                        open_points["_Reason"] = open_points.apply(
                            _historical_open_reason, axis=1
                        )

'''
text = text.replace(anchor, insert + anchor, 1)

# 4) Regular OPEN hover
old = '''                                customdata=regular_open_points[
                                    ["ticker"]
                                ],
                                hovertemplate=(
                                    "<b>%{customdata[0]}</b>"
                                    "<br>Simulator status: OPEN"
                                    "<br>%{x}"
                                    "<br>%{y:.2f}"
                                    "<extra></extra>"
                                ),
'''
new = '''                                customdata=regular_open_points[
                                    ["ticker", "_Action", "_Reason"]
                                ],
                                hovertemplate=(
                                    "<b>%{customdata[0]}</b>"
                                    "<br>Simulator status: OPEN"
                                    "<br>Action: %{customdata[1]}"
                                    "<br>Reason: %{customdata[2]}"
                                    "<br>%{x}"
                                    "<br>%{y:.2f}"
                                    "<extra></extra>"
                                ),
'''
if old not in text:
    raise SystemExit("ERROR: regular OPEN hover not found.")
text = text.replace(old, new, 1)

# 5) Re-entry hover
old = '''                                customdata=reentry_points[
                                    ["ticker"]
                                ],
                                hovertemplate=(
                                    "<b>%{customdata[0]}</b>"
                                    "<br>Re-BUY: SELL at previous "
                                    "15-minute point"
                                    "<br>Simulator status: OPEN"
                                    "<br>%{x}"
                                    "<br>%{y:.2f}"
                                    "<extra></extra>"
                                ),
'''
new = '''                                customdata=reentry_points[
                                    ["ticker", "_Action", "_Reason"]
                                ],
                                hovertemplate=(
                                    "<b>%{customdata[0]}</b>"
                                    "<br>Simulator status: OPEN"
                                    "<br>Action: %{customdata[1]}"
                                    "<br>Reason: %{customdata[2]}"
                                    "<br>%{x}"
                                    "<br>%{y:.2f}"
                                    "<extra></extra>"
                                ),
'''
if old not in text:
    raise SystemExit("ERROR: re-entry hover not found.")
text = text.replace(old, new, 1)

# 6) Counters + historical analysis steps
old = '''                    info_cols = st.columns(3)

                    info_cols[0].metric(
                        "Points",
                        len(chart_df),
                    )

                    info_cols[1].metric(
                        "From",
                        first_time.strftime(
                            "%d.%m %H:%M"
                        ),
                    )

                    info_cols[2].metric(
                        "To",
                        last_time.strftime(
                            "%d.%m %H:%M"
                        ),
                    )
'''
new = '''                    newest_historical = pd.to_datetime(
                        historical["timestamp"].max(),
                        utc=True,
                        errors="coerce",
                    )
                    newest_historical_text = (
                        newest_historical
                        .tz_convert(LOCAL_TIMEZONE)
                        .strftime("%H:%M %Z")
                        if pd.notna(newest_historical)
                        else "—"
                    )

                    info_cols = st.columns(4)

                    info_cols[0].metric(
                        "Newest data",
                        newest_historical_text,
                    )

                    info_cols[1].metric(
                        "From",
                        first_time.strftime(
                            "%d.%m %H:%M"
                        ),
                    )

                    info_cols[2].metric(
                        "To",
                        last_time.strftime(
                            "%d.%m %H:%M"
                        ),
                    )

                    info_cols[3].metric(
                        "Points",
                        len(chart_df),
                    )

                    st.subheader(
                        "Steps to analyse the ticker trading periods"
                    )
                    st.markdown(
                        "1. Are the start and end time points of the OPEN ticker "
                        "proposed by the simulator as expected?\\n"
                        "2. If not, review the ticker in the **Historical Data** and "
                        "**Last Data** pages to understand the behavior and update "
                        "the simulator rules that decide when to buy and sell the ticker."
                    )
'''
if old not in text:
    raise SystemExit("ERROR: Historical counters block not found.")
text = text.replace(old, new, 1)

compile(text, str(path), "exec")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(
    f"{path.name}.bak-before-historical-analysis-v1-{stamp}"
)
shutil.copy2(path, backup)
path.write_text(text, encoding="utf-8")

print("SUCCESS: Historical Data and Sim-Trading analysis updated.")
print(f"Backup: {backup}")
print("Counters: Newest data / From / To / Points")
print("Hover: Simulator status / Action / Reason")
print("Added Historical Data analysis steps.")

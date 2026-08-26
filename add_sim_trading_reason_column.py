#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

page_start = text.find('elif page == "Sim-Trading":')
page_end = text.find('elif page == "Resources":', page_start)

if page_start == -1 or page_end == -1:
    raise SystemExit(
        "ERROR: Sim-Trading page boundaries not found; no changes written."
    )

section = text[page_start:page_end]

# ------------------------------------------------------------
# 1) Include SellReason in the empty-schema definition.
# ------------------------------------------------------------
old_empty = '''                    "RelativeDifference",
                    "Status",
                ])
'''
new_empty = '''                    "RelativeDifference",
                    "SellReason",
                    "Status",
                ])
'''

if old_empty not in section:
    raise SystemExit(
        "ERROR: Sim-Trading empty simulation schema not found; no changes written."
    )
section = section.replace(old_empty, new_empty, 1)

# ------------------------------------------------------------
# 2) Include SellReason when merging the latest simulator trade.
# ------------------------------------------------------------
old_merge = '''                        "RelativeDifference",
                        "Status",
                    ]
'''
new_merge = '''                        "RelativeDifference",
                        "SellReason",
                        "Status",
                    ]
'''

if old_merge not in section:
    raise SystemExit(
        "ERROR: Sim-Trading merge column list not found; no changes written."
    )
section = section.replace(old_merge, new_merge, 1)

# ------------------------------------------------------------
# 3) Ensure SellReason exists in shown.
# ------------------------------------------------------------
old_defaults = '''                "RelativeDifference": pd.NA,
                "Status": pd.NA,
            }.items():
'''
new_defaults = '''                "RelativeDifference": pd.NA,
                "SellReason": pd.NA,
                "Status": pd.NA,
            }.items():
'''

if old_defaults not in section:
    raise SystemExit(
        "ERROR: Sim-Trading defaults block not found; no changes written."
    )
section = section.replace(old_defaults, new_defaults, 1)

# ------------------------------------------------------------
# 4) Add Reason immediately after SimStatus is calculated.
# ------------------------------------------------------------
anchor = '''            shown["SimStatus"] = shown["Status"].map(
                lambda value:
                str(value).strip().upper()
                if pd.notna(value) and str(value).strip()
                else "NO TRADE"
            )

'''

reason_block = '''            shown["SimStatus"] = shown["Status"].map(
                lambda value:
                str(value).strip().upper()
                if pd.notna(value) and str(value).strip()
                else "NO TRADE"
            )

            def sim_trade_reason(row):
                status = str(row.get("SimStatus") or "").strip().upper()

                if status == "OPEN":
                    # The simulation API currently persists SellReason but no
                    # equivalent BuyReason. "Buy signal" therefore describes
                    # the actual transition without inventing a non-persisted
                    # trigger.
                    return "Buy signal"

                if status == "CLOSED":
                    sell_reason = str(
                        row.get("SellReason") or ""
                    ).strip().upper()

                    if sell_reason in {"C4+C5", "C5+C4"}:
                        return "Drop + static"
                    if sell_reason == "C4":
                        return "Price drop"
                    if sell_reason == "C5":
                        return "Price static"
                    if sell_reason:
                        # Keep unknown backend reason concise.
                        return sell_reason[:24]
                    return "Sell signal"

                return "—"

            shown["Reason"] = shown.apply(
                sim_trade_reason,
                axis=1,
            )

'''

if anchor not in section:
    raise SystemExit(
        "ERROR: SimStatus calculation block not found; no changes written."
    )

section = section.replace(anchor, reason_block, 1)

# ------------------------------------------------------------
# 5) Insert Reason after TickerName in display table.
# ------------------------------------------------------------
old_display = '''                    "SimStatus",
                    "Ticker",
                    "TickerName",
                    "InitTime",
'''
new_display = '''                    "SimStatus",
                    "Ticker",
                    "TickerName",
                    "Reason",
                    "InitTime",
'''

if old_display not in section:
    raise SystemExit(
        "ERROR: Sim-Trading display column order not found; no changes written."
    )
section = section.replace(old_display, new_display, 1)

# ------------------------------------------------------------
# 6) Update explanatory note.
# ------------------------------------------------------------
old_caption = '''            st.caption(
                "SimStatus shows the ticker's latest simulator state: OPEN, CLOSED, "
                "or NO TRADE. InitTime and InitPrice are the latest simulated BUY "
                "time and EUR price. SellTime and SellPrice are populated when the "
                "latest simulated trade has been sold."
            )
'''

new_caption = '''            st.caption(
                "SimStatus shows the ticker's latest simulator state: OPEN, CLOSED, "
                "or NO TRADE. Reason gives a maximum-three-word explanation of the "
                "transition: OPEN uses 'Buy signal' because the simulator currently "
                "does not persist a separate BuyReason; CLOSED uses the stored "
                "SellReason (C4 = Price drop, C5 = Price static, C4+C5 = Drop + static). "
                "InitTime and InitPrice are the latest simulated BUY time and EUR price. "
                "SellTime and SellPrice are populated when the latest simulated trade "
                "has been sold."
            )
'''

if old_caption not in section:
    raise SystemExit(
        "ERROR: Sim-Trading first caption not found; no changes written."
    )
section = section.replace(old_caption, new_caption, 1)

text = text[:page_start] + section + text[page_end:]
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Sim-Trading Reason column added after TickerName; "
    "OPEN uses Buy signal and CLOSED maps persisted SellReason."
)

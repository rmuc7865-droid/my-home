#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# ------------------------------------------------------------
# 1) Sim-Trading EUR formatting: remove leading "+"
# ------------------------------------------------------------
old = '''            def format_eur(value):
                return f"€{float(value):+.2f}" if pd.notna(value) else "—"
'''

new = '''            def format_eur(value):
                return f"€{float(value):.2f}" if pd.notna(value) else "—"
'''

if old not in text:
    raise SystemExit(
        "ERROR: Sim-Trading format_eur block not found; no changes written."
    )

text = text.replace(old, new, 1)

# ------------------------------------------------------------
# 2) Sim-Trading sorting:
#    first SimStatus, then DiffLastPrice descending.
# ------------------------------------------------------------
old = '''            shown["_StatusSort"] = shown["SimStatus"].map(
                lambda value: status_order.get(value, 99)
            )
            shown["_InitSort"] = shown["BuyTime"]

            shown = shown.sort_values(
                by=["_StatusSort", "_InitSort", "Ticker"],
                ascending=[True, False, True],
                na_position="last",
            )
'''

new = '''            shown["_StatusSort"] = shown["SimStatus"].map(
                lambda value: status_order.get(value, 99)
            )
            shown["_DiffLastPriceSort"] = pd.to_numeric(
                shown["DiffLastPriceRaw"],
                errors="coerce",
            )

            shown = shown.sort_values(
                by=["_StatusSort", "_DiffLastPriceSort", "Ticker"],
                ascending=[True, False, True],
                na_position="last",
            )
'''

if old not in text:
    raise SystemExit(
        "ERROR: Sim-Trading sort block not found; no changes written."
    )

text = text.replace(old, new, 1)

# ------------------------------------------------------------
# 3) Update Sim-Trading notes to current parameters and sorting.
# ------------------------------------------------------------
old = '''            st.caption(
                "SimStatus is the state of the ticker's latest simulator trade: "
                "OPEN, CLOSED, or NO TRADE. InitTime and InitPrice are the latest "
                "simulated BUY time and EUR price. SellTime and SellPrice are the "
                "simulated SELL values when the latest trade is closed."
            )

            st.caption(
                "DiffSellTime = SellTime - InitTime. DiffSellPrice is the "
                "percentage change from InitPrice to SellPrice. LastTime and "
                "LastPrice are the latest collected market-data time and current "
                "EUR price. DiffLastTime = LastTime - InitTime. DiffLastPrice is "
                "the percentage change from InitPrice to LastPrice. Elapsed times "
                "are shown as D days HH:MM."
            )
'''

new = '''            st.caption(
                "SimStatus shows the ticker's latest simulator state: OPEN, CLOSED, "
                "or NO TRADE. InitTime and InitPrice are the latest simulated BUY "
                "time and EUR price. SellTime and SellPrice are populated when the "
                "latest simulated trade has been sold."
            )

            st.caption(
                "DiffSellTime = SellTime - InitTime. DiffSellPrice = percentage "
                "change from InitPrice to SellPrice. LastTime and LastPrice are the "
                "latest collected market-data time and EUR price. DiffLastTime = "
                "LastTime - InitTime. DiffLastPrice = percentage change from "
                "InitPrice to LastPrice. Elapsed times use D days HH:MM. Rows are "
                "sorted first by SimStatus and then by DiffLastPrice from highest "
                "to lowest."
            )
'''

if old not in text:
    raise SystemExit(
        "ERROR: Sim-Trading notes block not found; no changes written."
    )

text = text.replace(old, new, 1)

# ------------------------------------------------------------
# 4) Last Data LastPrice: exactly 2 decimal digits.
# ------------------------------------------------------------
old = '''                    return f"€{value:.3f}"
'''

new = '''                    return f"€{value:.2f}"
'''

if old not in text:
    raise SystemExit(
        "ERROR: Last Data LastPrice 3-decimal formatter not found; no changes written."
    )

text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Sim-Trading price formatting/sorting/notes updated; "
    "Last Data LastPrice now uses 2 decimals."
)

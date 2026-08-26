#!/usr/bin/env python3
from pathlib import Path
import re

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")
original = text

old = '''            def format_eur(value):
                return f"€{float(value):+.2f}" if pd.notna(value) else "—"
'''
new = '''            def format_eur(value):
                return f"€{float(value):.2f}" if pd.notna(value) else "—"
'''

if old in text:
    text = text.replace(old, new, 1)
elif 'def format_eur(value):' in text:
    pos = text.find('def format_eur(value):')
    nearby = text[pos:pos + 180]
    if ':+' in nearby:
        raise SystemExit("ERROR: Sim-Trading EUR formatter variant not recognized; no changes written.")
    print("INFO: Sim-Trading EUR formatter already has no '+' sign.")
else:
    raise SystemExit("ERROR: Sim-Trading format_eur block not found; no changes written.")

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

if old in text:
    text = text.replace(old, new, 1)
elif '_DiffLastPriceSort' in text:
    print("INFO: Sim-Trading DiffLastPrice sorting already applied.")
else:
    raise SystemExit("ERROR: Sim-Trading sort block not found; no changes written.")

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

if old in text:
    text = text.replace(old, new, 1)
elif "sorted first by SimStatus and then by DiffLastPrice" in text:
    print("INFO: Sim-Trading notes already updated.")
else:
    raise SystemExit("ERROR: Sim-Trading notes block not found; no changes written.")

last_data_start = text.find('st.subheader("Last Data")')
if last_data_start == -1:
    raise SystemExit("ERROR: Last Data section not found; no changes written.")

next_page = text.find('\nelif page ==', last_data_start)
last_data_end = next_page if next_page != -1 else len(text)
section = text[last_data_start:last_data_end]

patterns = [
    (r'f"€\{value:\.3f\}"', 'f"€{value:.2f}"'),
    (r'f"€\{float\(value\):\.3f\}"', 'f"€{float(value):.2f}"'),
    (r'f"€\{last_price:\.3f\}"', 'f"€{last_price:.2f}"'),
    (r'f"€\{float\(last_price\):\.3f\}"', 'f"€{float(last_price):.2f}"'),
]

changed_last_price = False
for pattern, replacement in patterns:
    new_section, count = re.subn(pattern, replacement, section, count=1)
    if count:
        section = new_section
        changed_last_price = True
        break

if not changed_last_price:
    if re.search(r'f"€\{(?:float\()?\w+(?:\))?:\.2f\}"', section):
        print("INFO: Last Data LastPrice already appears to use 2 decimals.")
    else:
        candidates = [
            line.strip()
            for line in section.splitlines()
            if '€' in line
        ][:12]
        raise SystemExit(
            "ERROR: Could not identify Last Data EUR formatter safely. Candidates: "
            + repr(candidates)
        )

text = text[:last_data_start] + section + text[last_data_end:]

if text == original:
    print("SUCCESS: all requested changes were already present; no write needed.")
else:
    path.write_text(text, encoding="utf-8")
    print(
        "SUCCESS: Sim-Trading prices/sorting/notes updated; "
        "Last Data LastPrice uses 2 decimals."
    )

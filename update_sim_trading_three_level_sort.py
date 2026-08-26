#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old = '''            shown["_StatusSort"] = shown["SimStatus"].map(
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

new = '''            shown["_StatusSort"] = shown["SimStatus"].map(
                lambda value: status_order.get(value, 99)
            )
            shown["_DiffSellPriceSort"] = pd.to_numeric(
                shown["DiffSellPriceRaw"],
                errors="coerce",
            )
            shown["_DiffLastPriceSort"] = pd.to_numeric(
                shown["DiffLastPriceRaw"],
                errors="coerce",
            )

            shown = shown.sort_values(
                by=[
                    "_StatusSort",
                    "_DiffSellPriceSort",
                    "_DiffLastPriceSort",
                    "Ticker",
                ],
                ascending=[True, False, False, True],
                na_position="last",
            )
'''

if old not in text:
    raise SystemExit(
        "ERROR: Current Sim-Trading DiffLastPrice sort block not found; no changes written."
    )

text = text.replace(old, new, 1)

old_note = '''                "InitPrice to LastPrice. Elapsed times use D days HH:MM. Rows are "
                "sorted first by SimStatus and then by DiffLastPrice from highest "
                "to lowest."
'''

new_note = '''                "InitPrice to LastPrice. Elapsed times use D days HH:MM. Rows are "
                "sorted first by SimStatus, then by DiffSellPrice, and then by "
                "DiffLastPrice; both percentage differences are ordered from "
                "highest to lowest."
'''

if old_note not in text:
    raise SystemExit(
        "ERROR: Current Sim-Trading sorting note not found; no changes written."
    )

text = text.replace(old_note, new_note, 1)

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Sim-Trading now sorts by SimStatus, DiffSellPrice, "
    "then DiffLastPrice; notes updated."
)

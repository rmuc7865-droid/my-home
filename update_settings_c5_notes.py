#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# 1) Improve the main Settings rule note.
old_note = '''    st.caption(
        "C1 uses market calendar/timezone and BUY windows. C2 uses the CloseB threshold and required ticker count. "
        "C4 uses movement_percent; C5 uses movement_percent and c5_hours. This dashboard version does not contain "
        "a C3 calculation, so there is no C3 parameter to edit here. SELL windows affect CanSellNow/SellTiming rather than C4/C5 themselves."
    )
'''

new_note = '''    st.caption(
        "Rule meaning: C1 checks whether the configured BUY market window is open. "
        "C2 checks whether enough active tickers meet the configured CloseB threshold. "
        "C4 becomes true after price has dropped by more than the configured movement threshold "
        "from the sampled peak. C5 becomes true only after the price has remained inside the "
        "configured +/- movement threshold around the current price for the entire configured "
        "static-price window. The setting stored internally as c5_hours is simply the length of "
        "that continuous C5 window, in hours. This dashboard version has no C3 calculation. "
        "SELL windows control when a sell can be executed (CanSellNow/SellTiming); they do not "
        "change the C4 or C5 calculations themselves."
    )
'''

if old_note not in text:
    raise SystemExit(
        "ERROR: Settings rule note not found; no changes written."
    )

text = text.replace(old_note, new_note, 1)

# 2) Rename the C5 input and add clear help text.
old_input = '''            new_c5_hours = st.number_input(
                "C5: trailing window (hours)",
                min_value=0.25,
                max_value=720.0,
                value=float(editable_sell.get("c5_hours", 24.0)),
                step=0.25,
                format="%.2f",
            )
'''

new_input = '''            new_c5_hours = st.number_input(
                "C5: static-price window (hours)",
                min_value=0.25,
                max_value=720.0,
                value=float(editable_sell.get("c5_hours", 24.0)),
                step=0.25,
                format="%.2f",
                help=(
                    "Internally this setting is named c5_hours. It is the minimum continuous "
                    "time period for which all sampled prices must remain within +/- the "
                    "configured C4/C5 movement threshold around the current price before C5 "
                    "becomes true. Example: with 24 hours and a 1.1% threshold, C5 is true only "
                    "after a full 24 hours in which every sampled price stayed within +/-1.1% "
                    "of the current price."
                ),
            )
'''

if old_input not in text:
    raise SystemExit(
        "ERROR: C5 number_input block not found; no changes written."
    )

text = text.replace(old_input, new_input, 1)

# 3) Clarify validation wording.
old_validation = '''            errors.append("C5 window must be between 0.25 and 720 hours.")
'''
new_validation = '''            errors.append("C5 static-price window must be between 0.25 and 720 hours.")
'''

if old_validation in text:
    text = text.replace(old_validation, new_validation, 1)

# 4) Add a compact parameter-name note below the form heading if absent.
anchor = '''        st.markdown("**Market calendars and trading windows (C1 / CanSellNow)**")
'''
parameter_note = '''        st.caption(
            "Parameter names: movement_percent = the percentage threshold shared by C4 and C5; "
            "c5_hours = the C5 static-price window length in hours."
        )

'''

if parameter_note not in text:
    if anchor not in text:
        raise SystemExit(
            "ERROR: Settings market-calendar anchor not found; no changes written."
        )
    text = text.replace(anchor, parameter_note + anchor, 1)

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Settings notes updated; c5_hours is now explained as the "
    "C5 static-price window and the visible field label was clarified."
)

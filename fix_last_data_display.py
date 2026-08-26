#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

replacements = [
    (
        """                        "RecordsToday": st.column_config.NumberColumn(
                            "RecordsToday",
                            format="%d",
                        ),
                        "C1": st.column_config.CheckboxColumn("C1"),
                        "C2": st.column_config.CheckboxColumn("C2"),
                        "C4": st.column_config.CheckboxColumn("C4"),
                        "C5": st.column_config.CheckboxColumn("C5"),""",
        """                        "Records": st.column_config.NumberColumn(
                            "Records",
                            format="%d",
                        ),"""
    ),
    (
        """            "bar, while LastPrice is its estimated EUR price. RecordsToday counts unique market """,
        """            "bar, while LastPrice is its estimated EUR price. Records counts unique market """
    ),
    (
        """            "LastSellTime is the estimated time needed to sell a EUR 10,000 position using """,
        """            "SellingTime is the estimated time needed to sell a EUR 10,000 position using """
    ),
    (
        """            "DropPrice is the percentage fall from the peak selected by C4. ChangePrice is the "
            "maximum absolute percentage deviation from the current price over the configured "
            "C5 lookback window." """,
        """            "DropDuration is the shortest elapsed period ending at LastCollect during which price "
            "did not exceed LastPrice by more than the configured C4/C5 movement threshold. "
            "StaticDuration is the shortest elapsed period ending at LastCollect during which price "
            "stayed inside the configured +/- movement-threshold band around LastPrice. "
            "WaitToTrade and WaitToOpening show the remaining time to the relevant trading phase." """
    ),
]

changed = 0
for old, new in replacements:
    if old in text:
        text = text.replace(old, new, 1)
        changed += 1
    else:
        print("WARNING: pattern not found:")
        print(old.splitlines()[0])

path.write_text(text, encoding="utf-8")
print(f"Updated {changed}/{len(replacements)} Last Data display/caption blocks in {path}")

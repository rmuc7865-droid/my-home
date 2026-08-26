#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

replacements = [
    (
        '"Effective Trading"',
        '"Resources"',
    ),
    (
        'elif page == "Effective Trading":',
        'elif page == "Resources":',
    ),
    (
        'st.header("Effective Trading")',
        'st.header("Resources")',
    ),
]

changed = 0

# Rename sidebar/page-label occurrence carefully.
if '"Effective Trading"' in text:
    text = text.replace('"Effective Trading"', '"Resources"', 1)
    changed += 1

if 'elif page == "Effective Trading":' in text:
    text = text.replace(
        'elif page == "Effective Trading":',
        'elif page == "Resources":',
        1,
    )
    changed += 1

if 'st.header("Effective Trading")' in text:
    text = text.replace(
        'st.header("Effective Trading")',
        'st.header("Resources")',
        1,
    )
    changed += 1

if changed < 2:
    raise SystemExit(
        f"ERROR: Expected Effective Trading page markers were not found completely "
        f"(changed={changed}); no changes written."
    )

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Effective Trading page renamed to Resources; page logic unchanged."
)

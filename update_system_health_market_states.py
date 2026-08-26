#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# ------------------------------------------------------------------
# 1) Extend dashboard_status_summary with explicit Market states.
# ------------------------------------------------------------------
old_result = '''        "collector_bad": [],
        "market_bad": [],
        "open_alerts": 0,
'''
new_result = '''        "collector_bad": [],
        "market_bad": [],
        "market_stale": [],
        "market_offline": [],
        "market_state": "OFFLINE",
        "open_alerts": 0,
'''

if old_result not in text:
    raise SystemExit("ERROR: dashboard status result block not found; no changes written.")
text = text.replace(old_result, new_result, 1)

old_market = '''    result["market_bad"] = status_latest.loc[
        status_latest["MarketStatus"] != "OK", "system"
    ].astype(str).tolist()
    result["collectors_ok"] = not result["collector_bad"]
    result["market"] = not result["market_bad"]
'''
new_market = '''    result["market_bad"] = status_latest.loc[
        status_latest["MarketStatus"] != "OK", "system"
    ].astype(str).tolist()
    result["market_stale"] = status_latest.loc[
        status_latest["MarketStatus"] == "STALE", "system"
    ].astype(str).tolist()
    result["market_offline"] = status_latest.loc[
        status_latest["MarketStatus"] == "OFFLINE", "system"
    ].astype(str).tolist()

    result["collectors_ok"] = not result["collector_bad"]
    result["market"] = not result["market_bad"]

    if result["market_offline"]:
        result["market_state"] = "OFFLINE"
    elif result["market_stale"]:
        result["market_state"] = "STALE"
    elif not status_latest.empty:
        result["market_state"] = "OK"
'''

if old_market not in text:
    raise SystemExit("ERROR: Market status calculation block not found; no changes written.")
text = text.replace(old_market, new_market, 1)

# ------------------------------------------------------------------
# 2) Market circle: green / orange / red.
# ------------------------------------------------------------------
old_color = '''    healthy_color = "#21c55d" if status["healthy"] else "#ef4444"
    market_color = "#21c55d" if status["market"] else "#ef4444"
'''
new_color = '''    healthy_color = "#21c55d" if status["healthy"] else "#ef4444"

    market_color = {
        "OK": "#21c55d",
        "STALE": "#f59e0b",
        "OFFLINE": "#ef4444",
    }.get(status.get("market_state"), "#ef4444")
'''

if old_color not in text:
    raise SystemExit("ERROR: Header Market color block not found; no changes written.")
text = text.replace(old_color, new_color, 1)

old_tip = '''    market_tip = (
        "All active systems have MarketStatus OK"
        if status["market"]
        else "MarketStatus not OK: " + (", ".join(status["market_bad"]) or "no active systems")
    )
'''
new_tip = '''    if status.get("market_state") == "OK":
        market_tip = "All relevant non-crypto systems have MarketStatus OK"
    elif status.get("market_state") == "STALE":
        market_tip = (
            "MarketStatus STALE: "
            + (", ".join(status["market_stale"]) or "unknown system")
        )
    else:
        market_tip = (
            "MarketStatus OFFLINE: "
            + (", ".join(status["market_offline"]) or ", ".join(status["market_bad"]) or "no relevant systems")
        )
'''

if old_tip not in text:
    raise SystemExit("ERROR: Header Market tooltip block not found; no changes written.")
text = text.replace(old_tip, new_tip, 1)

# ------------------------------------------------------------------
# 3) Replace System Health explanatory note.
#    Use stable text from current implementation.
# ------------------------------------------------------------------
old_note = '''        st.caption(
            "CollectorStatus is based on when "
            "the server last received a record. "
            "MarketStatus is based on the timestamp "
            "of the latest market observation. "
            "If CollectorStatus is OK but MarketStatus "
            "is STALE or OFFLINE, the collector and "
            "network connection are working; check the "
            "upstream market-data provider or the age "
            "of the provider's latest bar."
        )
'''

new_note = '''        st.caption(
            "Status meaning: OK = the latest timestamp is no more than 30 minutes old. "
            "STALE = the latest timestamp is more than 30 minutes but no more than "
            "120 minutes old; the source is delayed and should be treated cautiously. "
            "OFFLINE = the latest timestamp is more than 120 minutes old (or unavailable); "
            "the source should be treated as unavailable until fresh data arrives. "
            "CollectorStatus uses the time the server last received a record. "
            "MarketStatus uses the timestamp of the latest market observation. "
            "Therefore, CollectorStatus can be OK while MarketStatus is STALE/OFFLINE "
            "when the collector/network is working but the upstream provider is not "
            "delivering fresh market bars."
        )

        system_descriptions = {
            "polygon": (
                "Polygon: US stock market-data collector used for the ZERO stock "
                "trading analysis and simulator."
            ),
            "crypto": (
                "Crypto: cryptocurrency market-data collector. It is shown on this "
                "System Health page, but it is intentionally excluded from the "
                "Healthy/Market header indicators."
            ),
            "international": (
                "International: international-market collector. Its status matters "
                "only when that collector is enabled and actively reporting."
            ),
        }

        shown_systems = (
            display_health["system"]
            .dropna()
            .astype(str)
            .str.strip()
            .tolist()
        )

        system_notes = []
        for system_name in shown_systems:
            normalized = system_name.lower()
            system_notes.append(
                system_descriptions.get(
                    normalized,
                    f"{system_name}: reporting system/source shown in the health table.",
                )
            )

        if system_notes:
            st.caption(
                "Systems: " + " ".join(system_notes)
            )
'''

if old_note not in text:
    raise SystemExit("ERROR: System Health note block not found; no changes written.")
text = text.replace(old_note, new_note, 1)

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Market header is green/STALE-orange/OFFLINE-red; "
    "System Health status definitions and per-system notes added."
)

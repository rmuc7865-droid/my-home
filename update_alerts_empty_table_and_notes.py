#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old = '''elif page == "Alerts":
    if alerts_df.empty:
        st.success("No alerts recorded.")
    else:
        alerts_df["created_at"] = pd.to_datetime(alerts_df["created_at"], utc=True)
        show_open = st.toggle("Only unacknowledged", value=True)
        shown = alerts_df[~alerts_df["acknowledged"]] if show_open else alerts_df
        st.dataframe(
            shown[["id", "created_at", "severity", "system", "rule_name", "actual_value", "acknowledged"]],
            use_container_width=True,
            hide_index=True,
        )
        alert_id = st.number_input("Alert ID to acknowledge", min_value=1, step=1)
        if st.button("Acknowledge alert"):
            try:
                api_post(f"/api/v1/alerts/{int(alert_id)}/acknowledge")
                st.success("Alert acknowledged.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

'''

new = '''elif page == "Alerts":
    alert_columns = [
        "id",
        "created_at",
        "severity",
        "system",
        "rule_name",
        "actual_value",
        "acknowledged",
    ]

    alerts_work = alerts_df.copy()

    # Keep the expected schema even when there are currently zero rows.
    for column in alert_columns:
        if column not in alerts_work.columns:
            if column == "acknowledged":
                alerts_work[column] = pd.Series(dtype="bool")
            elif column == "id":
                alerts_work[column] = pd.Series(dtype="Int64")
            else:
                alerts_work[column] = pd.Series(dtype="object")

    alerts_work["created_at"] = pd.to_datetime(
        alerts_work["created_at"],
        utc=True,
        errors="coerce",
    )

    alerts_work["acknowledged"] = (
        alerts_work["acknowledged"]
        .fillna(False)
        .astype(bool)
    )

    show_open = st.toggle(
        "Only unacknowledged",
        value=True,
    )

    shown = (
        alerts_work[~alerts_work["acknowledged"]].copy()
        if show_open
        else alerts_work.copy()
    )

    if shown.empty:
        st.success(
            "No unacknowledged alerts."
            if show_open
            else "No alerts recorded."
        )

    st.dataframe(
        shown[alert_columns],
        use_container_width=True,
        hide_index=True,
    )

    st.caption(
        "Columns: id = unique alert identifier; created_at = time the alert was created; "
        "severity = importance/urgency; system = collector or subsystem that raised the alert; "
        "rule_name = rule or condition that triggered it; actual_value = measured value/details "
        "that caused the rule to fire; acknowledged = whether the alert has already been reviewed."
    )

    st.caption(
        "Alert types are represented mainly by rule_name and severity. Typical types include "
        "collector/data-freshness alerts when a source stops reporting or becomes stale; "
        "market-data alerts when the latest market observation is too old; threshold/rule alerts "
        "when a configured condition is exceeded; and system/configuration alerts when a service, "
        "setting, or expected data source is unavailable. The exact rule_name identifies the "
        "specific condition that fired."
    )

    if not alerts_work.empty:
        alert_id = st.number_input(
            "Alert ID to acknowledge",
            min_value=1,
            step=1,
        )
        if st.button("Acknowledge alert"):
            try:
                api_post(
                    f"/api/v1/alerts/{int(alert_id)}/acknowledge"
                )
                st.success("Alert acknowledged.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

'''

if old not in text:
    raise SystemExit(
        "ERROR: Current Alerts page block not found; no changes written."
    )

text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Alerts table is always visible, including with zero rows; "
    "column and alert-type notes added."
)

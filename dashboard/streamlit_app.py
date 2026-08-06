from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

API_URL = os.getenv("MONITOR_API_URL", "http://api:8000")
API_KEY = os.getenv("MONITOR_API_KEY", "CHANGE_ME")
HEADERS = {"X-API-Key": API_KEY}

st.set_page_config(page_title="Home Monitor", page_icon="🏠", layout="wide")
st.title("🏠 Home Monitor")


def api_get(path: str, params: dict | None = None):
    response = httpx.get(f"{API_URL}{path}", headers=HEADERS, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def api_post(path: str):
    response = httpx.post(f"{API_URL}{path}", headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()


try:
    measurements = api_get("/api/v1/measurements", {"limit": 2000})
    alerts = api_get("/api/v1/alerts", {"limit": 500})
except Exception as exc:
    st.error(f"Cannot reach monitoring API: {exc}")
    st.stop()

measurement_rows: list[dict] = []
for record in measurements:
    base = {
        "id": record["id"],
        "system": record["system"],
        "device": record["device"],
        "timestamp": pd.to_datetime(record["timestamp"], utc=True),
    }
    measurement_rows.append({**base, **record["measurements"]})

df = pd.DataFrame(measurement_rows)
alerts_df = pd.DataFrame(alerts)

page = st.sidebar.radio("Page", ["Live Overview", "Alerts", "Historical Trends", "System Health"])
if st.sidebar.button("Refresh now", use_container_width=True):
    st.rerun()
st.sidebar.caption(f"Last loaded: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

if page == "Live Overview":
    if df.empty:
        st.info("No measurements received yet.")
    else:
        latest = df.sort_values("timestamp").groupby("system", as_index=False).tail(1)
        open_alerts = 0 if alerts_df.empty else int((~alerts_df["acknowledged"]).sum())
        cols = st.columns(4)
        cols[0].metric("Systems", latest["system"].nunique())
        cols[1].metric("Measurements", len(df))
        cols[2].metric("Open alerts", open_alerts)
        newest = latest["timestamp"].max()
        cols[3].metric("Newest data", newest.strftime("%H:%M UTC"))
        st.subheader("Latest values")
        st.dataframe(latest.drop(columns=["id"], errors="ignore"), use_container_width=True, hide_index=True)

elif page == "Alerts":
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

elif page == "Historical Trends":
    if df.empty:
        st.info("No historical data available.")
    else:
        system = st.selectbox("System", sorted(df["system"].unique()))
        system_df = df[df["system"] == system].sort_values("timestamp")
        numeric_columns = [
            column for column in system_df.select_dtypes(include="number").columns if column != "id"
        ]
        if not numeric_columns:
            st.info("This system has no numeric measurements to chart.")
        else:
            fields = st.multiselect("Measurements", numeric_columns, default=numeric_columns[:3])
            if fields:
                long_df = system_df.melt(
                    id_vars=["timestamp"], value_vars=fields, var_name="measurement", value_name="value"
                )
                figure = px.line(long_df, x="timestamp", y="value", color="measurement", markers=True)
                st.plotly_chart(figure, use_container_width=True)

elif page == "System Health":
    if df.empty:
        st.warning("No systems have reported data.")
    else:
        now = pd.Timestamp.now(tz="UTC")
        latest = df.groupby("system")["timestamp"].max().reset_index()
        latest["age_minutes"] = (now - latest["timestamp"]).dt.total_seconds() / 60
        latest["status"] = latest["age_minutes"].apply(
            lambda age: "OK" if age <= 30 else ("STALE" if age <= 120 else "OFFLINE")
        )
        st.dataframe(latest, use_container_width=True, hide_index=True)

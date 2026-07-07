'''
  - Connects to MinIO via DuckDB (no separate database needed).
  - Reads the GOLD ZONE Parquet files (aggregated, dashboard-ready data).
  - Lets the user:
      * pick a time grain (15min / hourly / daily)
      * pick a level ( per hall / per station)
      * filter by date range, hall, and station
  - Shows:
      * a time-series line chart of total energy consumption
      * markers highlighting detected PEAKS
      * a table of the underlying data
 
HOW TO RUN:
  This file is run automatically by the 'streamlit' service in
  docker-compose.yml. You don't need to run it manually.
 
  If you want to test it locally (outside Docker), you would run:
      streamlit run app.py
  but it needs the same environment variables as the Docker setup
  (MINIO_ENDPOINT, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD).
'''
import os
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import date, datetime

from queries import (
    query_overview, query_by_hall, query_heatmap,
    query_daily_peak, query_station_drilldown, query_hall_drilldown
)
from charts import (
    fig_overview, fig_by_hall, fig_heatmap,
    fig_daily_peak, fig_station_drilldown, fig_hall_drilldown
)

HALLS       = ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8"]
GRANULARITY = {"15 min": "minute", "Hour": "hour", "Day": "day"}

st.set_page_config(page_title="Energy Dashboard", page_icon="assets/favicon.jpg", layout="wide")

def load_css(path):
    with open(path) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

load_css(Path(__file__).parent / "style.css")

# Sidebar 
with st.sidebar:
    st.image(str(Path(__file__).parent / "assets" / "arnold-logo.png"), width=220)
    st.markdown('<div class="section-title">Global Filters</div>', unsafe_allow_html=True)

    date_range = st.date_input(
        "Date Range",
        value=(date(2025, 1, 1), date(2025, 1, 31)),
        min_value=date(2024, 1, 1), max_value=date.today(),
    )
    start_date, end_date = (date_range if len(date_range) == 2
                            else (date_range[0], date_range[0]))

    selected_halls = st.multiselect("Halls", options=HALLS, default=HALLS)
    if not selected_halls:
        selected_halls = HALLS

    gran_label = st.radio("Granularity", list(GRANULARITY.keys()), index=1, horizontal=True)
    trunc      = GRANULARITY[gran_label]

    st.markdown('<div class="section-title">Peak Drill-Down</div>', unsafe_allow_html=True)
    drill_hall = st.selectbox("Hall to investigate", options=HALLS)
    drill_date = st.date_input("Peak date", value=date(2025, 1, 15))

    col_a, col_b = st.columns(2)
    with col_a:
        t_from = st.time_input("From", value=datetime.strptime("08:00", "%H:%M").time())
    with col_b:
        t_to   = st.time_input("To",   value=datetime.strptime("12:00", "%H:%M").time())

    top_n = st.slider("Top N peak days", min_value=3, max_value=10, value=6)

# Load data 
start_str  = str(start_date)
end_str    = str(end_date)
peak_start = f"{drill_date} {t_from}"
peak_end   = f"{drill_date} {t_to}"

with st.spinner("Loading from MinIO…"):
    df_overview = query_overview(start_str, end_str, selected_halls, trunc)
    df_by_hall  = query_by_hall(start_str, end_str, selected_halls)
    df_heatmap  = query_heatmap(start_str, end_str, selected_halls)
    df_daily    = query_daily_peak(start_str, end_str, selected_halls)
    df_hall     = query_hall_drilldown(drill_hall, peak_start, peak_end)

# ── KPI cards ────────────────────────────────────────────────────
st.markdown("## ⚡ Energy Consumption Dashboard")

total   = df_overview["total_consumption"].sum()
peak    = df_overview["peak_max"].max()
p95     = df_overview["total_consumption"].quantile(0.95)
n_peaks = int((df_overview["total_consumption"] >= p95).sum())

for col, label, value, unit in zip(
    st.columns(4),
    ["Total Consumption", "Absolute Peak", "P95 Threshold", "Intervals Above P95"],
    [f"{total:,.0f}", f"{peak:,.1f}", f"{p95:,.1f}", str(n_peaks)],
    ["kWh", "kWh", "kWh", "intervals"],
):
    col.markdown(f"""
    <div class="metric-card">
        <h3>{label}</h3>
        <p>{value} <span style="font-size:14px;color:#8b949e">{unit}</span></p>
    </div>""", unsafe_allow_html=True)

st.markdown("---")

# Charts 
st.markdown("### Total Consumption Over Time")
st.plotly_chart(fig_overview(df_overview), use_container_width=True)

st.markdown("### Consumption by Hall")
st.plotly_chart(fig_by_hall(df_by_hall), use_container_width=True)

st.markdown("### Consumption Heatmap - Hall × Hour of Day")
st.plotly_chart(fig_heatmap(df_heatmap), use_container_width=True)

st.markdown("---")

st.markdown("### Daily Total Consumption + Peak Days")
st.plotly_chart(fig_daily_peak(df_daily, top_n), use_container_width=True)

st.markdown("---")

# initialize persistent storage once
if "selected_station_ids" not in st.session_state:
    st.session_state.selected_station_ids = []
    st.session_state.selected_station_names = []

st.markdown(f"### Hall Drill-Down - {drill_hall} · {peak_start} → {peak_end}")
if df_hall.empty:
    st.warning("No data for this hall / time window.")
else:
    st.plotly_chart(fig_hall_drilldown(df_hall), use_container_width=True)

    station_totals = (
        df_hall.groupby(["station_id", "station_name"])["total_consumption"]
        .sum().sort_values(ascending=False).reset_index()
        .rename(columns={"station_id": "ID", "total_consumption": "Total (kWh)", "station_name": "Station"})
    )

    event = st.dataframe(
        station_totals.drop(columns=["ID"]),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="station_table",
    )

    # always sync session_state to current selection — including when it's empty
    if event and event.selection:
        selected_rows = event.selection.rows
        selected_df = station_totals.iloc[selected_rows]
        st.session_state.selected_station_ids   = selected_df["ID"].tolist()
        st.session_state.selected_station_names = selected_df["Station"].tolist()

    station_ids   = st.session_state.selected_station_ids
    station_names = st.session_state.selected_station_names

    if station_ids:
        st.markdown("---")
        st.markdown(f"### Station Drill-Down - Number of stations selected: {len(station_names)} · {peak_start} → {peak_end}")
        st.caption(", ".join(station_names))

        ts_frames = []
        for sid, sname in zip(station_ids, station_names):
            df_ts = query_station_drilldown(sid, peak_start, peak_end)
            if not df_ts.empty:
                df_ts["station_name"] = sname
                ts_frames.append(df_ts)

        if not ts_frames:
            st.warning("No data for the selected stations / time window.")
        else:
            df_ts_combined = pd.concat(ts_frames, ignore_index=True)
            st.plotly_chart(fig_station_drilldown(df_ts_combined), use_container_width=True)
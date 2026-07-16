'''
STREAMLIT DASHBOARD
 
WHAT THIS APP DOES:
  - Connects to MinIO via DuckDB (no separate database needed).
  - Reads the GOLD ZONE Parquet files (aggregated, dashboard-ready data).
  - Lets the user:
      * pick a time grain (15min / hourly / daily)
      * pick a level ( per hall / per meter)
      * filter by date range, hall, and meter
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
    query_daily_peak, query_meter_drilldown, query_hall_drilldown,
    query_h71_daily_totals, query_h71_top_day, query_h71_machine_breakdown,
    query_h71_machine_timeseries, find_peak_interval
)
from charts import (
    fig_overview, fig_by_hall, fig_heatmap,
    fig_daily_peak, fig_meter_drilldown, fig_hall_drilldown, fig_peak_contributors,
    fig_h71_daily_totals, fig_h71_machine_breakdown, fig_h71_machine_timeseries
)

HALLS       = ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8"]
GRANULARITY = {"15 min": "minute", "Hour": "hour", "Day": "day"}

ROOT = Path(__file__).resolve().parent.parent

st.set_page_config(page_title="Energy Dashboard", page_icon=str(ROOT / "assets" / "favicon.jpg"), layout="wide")

def load_css(path):
    with open(path) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

load_css(Path(__file__).parent / "style.css")

# Sidebar 
with st.sidebar:
    st.image(str((ROOT / "assets" / "arnold-logo.png")), width=220)
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

# KPI cards 
st.markdown("## ⚡ Energy Consumption Dashboard")

total   = df_overview["total_consumption"].sum()
peak    = df_overview["peak_max"].max()
p95     = df_overview["total_consumption"].quantile(0.95)
n_peaks = int((df_overview["total_consumption"] >= p95).sum())

for col, label, value, unit in zip(
    st.columns(4),
    ["Total Consumption", "Absolute Peak", "P95 Threshold", "Intervals Above P95"],
    [f"{total:,.0f}", f"{peak:,.1f}", f"{p95:,.1f}", str(n_peaks)],
    ["kW", "kW", "kW", "intervals"],
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
if "selected_meter_ids" not in st.session_state:
    st.session_state.selected_meter_ids = []
    st.session_state.selected_meter_names = []

st.markdown(f"### Hall Drill-Down - {drill_hall} · {peak_start} → {peak_end}")
if df_hall.empty:
    st.warning("No data for this hall / time window.")
else:
    st.plotly_chart(fig_hall_drilldown(df_hall), use_container_width=True)

    st.markdown("### Which meter caused this peak?")
    st.plotly_chart(fig_peak_contributors(df_hall), use_container_width=True)

    meter_totals = (
        df_hall.groupby(["meter_id", "meter_name"])["total_consumption"]
        .sum().sort_values(ascending=False).reset_index()
        .rename(columns={"meter_id": "ID", "total_consumption": "Total (kW)", "meter_name": "Meter"})
    )

    event = st.dataframe(
        meter_totals.drop(columns=["ID"]),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="meter_table",
    )

    # always sync session_state to current selection — including when it's empty
    if event and event.selection:
        selected_rows = event.selection.rows
        selected_df = meter_totals.iloc[selected_rows]
        st.session_state.selected_meter_ids   = selected_df["ID"].tolist()
        st.session_state.selected_meter_names = selected_df["Meter"].tolist()

    meter_ids   = st.session_state.selected_meter_ids
    meter_names = st.session_state.selected_meter_names

    if meter_ids:
        st.markdown("---")
        st.markdown(f"### Meter Drill-Down - Number of meters selected: {len(meter_names)} · {peak_start} → {peak_end}")
        st.caption(", ".join(meter_names))

        ts_frames = []
        for sid, sname in zip(meter_ids, meter_names):
            df_ts = query_meter_drilldown(sid, peak_start, peak_end)
            if not df_ts.empty:
                df_ts["meter_name"] = sname
                ts_frames.append(df_ts)

        if not ts_frames:
            st.warning("No data for the selected meters / time window.")
        else:
            df_ts_combined = pd.concat(ts_frames, ignore_index=True)
            st.plotly_chart(fig_meter_drilldown(df_ts_combined), use_container_width=True)

# H7 New dataset (H71) THREE-STEP DRILL-DOWN
# Answers, end-to-end and automatically, for the fact_measurement_h71 gold
# table: which day peaked -> which machine drove it -> which interval on
# that machine peaked. No sidebar filters involved - this always looks at
# the full year of H7 new data.
st.markdown("---")
st.markdown("## Hall 7 (New Dataset) - Peak Drill-Down (Day → Machine → Interval)")
 
with st.spinner("Analyzing Hall 7…"):
    df_h71_daily        = query_h71_daily_totals()
    top_day, top_day_val = query_h71_top_day()
    df_h71_machines      = query_h71_machine_breakdown(top_day)
 
if df_h71_daily.empty or df_h71_machines.empty:
    st.warning("No data found in fact_measurement_h71.")
else:
    top_machine_id, top_machine_name, top_machine_val = (
        df_h71_machines.iloc[0]["meter_id"],
        df_h71_machines.iloc[0]["meter_name"],
        float(df_h71_machines.iloc[0]["total_consumption"]),
    )
 
    df_h71_ts = query_h71_machine_timeseries(top_day, top_machine_id)
    peak_start, peak_end, peak_val = find_peak_interval(df_h71_ts)
 
    top_day_fmt = pd.Timestamp(top_day).strftime("%d.%m.%Y")
    interval_fmt = (
        f"{peak_start.strftime('%H:%M')}–{peak_end.strftime('%H:%M')}"
        if peak_start is not None else "—"
    )
 
    # KPI summary of the three answers
    for col, label, value, sub in zip(
        st.columns(3),
        ["Peak Day", "Top Machine", "Peak Interval"],
        [top_day_fmt, top_machine_name, interval_fmt],
        [f"{top_day_val:,.1f} kW total", f"{top_machine_val:,.1f} kW that day", f"{peak_val:,.2f} kW" if peak_val else ""],
    ):
        col.markdown(f"""
        <div class="metric-card">
            <h3>{label}</h3>
            <p>{value}<br><span style="font-size:13px;color:#8b949e">{sub}</span></p>
        </div>""", unsafe_allow_html=True)
 
    st.markdown("#### Daily total consumption across the year")
    st.plotly_chart(fig_h71_daily_totals(df_h71_daily, top_day), use_container_width=True)
 
    st.markdown(f"#### Per-machine breakdown on {top_day_fmt}")
    st.plotly_chart(fig_h71_machine_breakdown(df_h71_machines), use_container_width=True)
 
    st.markdown(f"#### {top_machine_name}: consumption on {top_day_fmt}")
    if df_h71_ts.empty:
        st.warning("No readings for this machine on the peak day.")
    else:
        st.plotly_chart(
            fig_h71_machine_timeseries(df_h71_ts, peak_start, peak_end, peak_val),
            use_container_width=True,
        )
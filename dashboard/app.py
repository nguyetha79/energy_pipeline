'''
  - Connects to MinIO via DuckDB (no separate database needed).
  - Reads the GOLD ZONE Parquet files (aggregated, dashboard-ready data).
  - Lets the user pick a hall and a date range, then answers, end-to-end
    and automatically:
      1. Which day in the range had the highest total consumption?
      2. Which machine contributed most to consumption on that day?
      3. Which time interval was the peak for that machine on that day?
'''
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import date
import base64
 
from queries import (
    query_halls, query_daily_totals, query_top_day, query_machine_breakdown,
    query_machine_timeseries, find_peak_interval
)
from charts import (
    fig_daily_totals, fig_machine_breakdown, fig_machine_timeseries,
)

ROOT = Path(__file__).resolve().parent.parent
 
st.set_page_config(page_title="Energy Dashboard", page_icon=str(ROOT / "assets" / "favicon.jpg"), layout="wide")
 
 
def load_css(path):
    with open(path) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
 
load_css(Path(__file__).parent / "style.css")


# Logo
def img_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

logo_b64 = img_to_base64(str(ROOT / "assets" / "arnold-logo.png"))


# Header
st.markdown(
    f"""
    <div class="app-header">
        <div class="app-header-left">
            <img src="data:image/png;base64,{logo_b64}" class="app-header-logo">
            <div class="app-header-divider"></div>
            <div>
                <div class="app-header-title">⚡ Energy Consumption Dashboard</div>
                <div class="app-header-subtitle">Peak Drill-Down &nbsp;·&nbsp; Day → Machine → Interval</div>
            </div>
        </div>
        <div class="app-header-accent"></div>
    </div>
    """,
    unsafe_allow_html=True,
)


# Filters 
halls = query_halls()
 
col_hall, col_dates = st.columns([1, 2])
with col_hall:
    if halls:
        selected_hall = st.selectbox("Hall", options=halls)
    else:
        st.warning("No halls found in fact_measurement.")
        st.stop()
with col_dates:
    date_range = st.date_input(
        "Date Range",
        value=(date(2025, 1, 1), date(2025, 12, 31)),
        min_value=date(2024, 1, 1), max_value=date.today(),
    )
start_date, end_date = (date_range if len(date_range) == 2
                        else (date_range[0], date_range[0]))
 
start_str = str(start_date)
end_str   = str(end_date)


# Drill down
st.markdown(f"## Peak Drill-Down - {selected_hall} · {start_str} → {end_str}")
 
with st.spinner(f"Analyzing {selected_hall}…"):
    df_daily              = query_daily_totals(selected_hall, start_str, end_str)
    top_day, top_day_val  = query_top_day(selected_hall, start_str, end_str)
 
if df_daily.empty or top_day is None:
    st.warning(f"No data found for {selected_hall} in the selected date range.")
else:
    df_machines = query_machine_breakdown(selected_hall, top_day)
 
    if df_machines.empty:
        st.warning("No machine-level data found for the peak day.")
    else:
        top_machine_id, top_machine_name, top_machine_val = (
            df_machines.iloc[0]["meter_id"],
            df_machines.iloc[0]["meter_name"],
            float(df_machines.iloc[0]["total_consumption"]),
        )
 
        df_ts = query_machine_timeseries(selected_hall, top_machine_id, top_day)
        peak_start, peak_end, peak_val = find_peak_interval(df_ts)
 
        top_day_fmt = pd.Timestamp(top_day).strftime("%d.%m.%Y")
        interval_fmt = (
            f"{peak_start.strftime('%H:%M')}-{peak_end.strftime('%H:%M')}"
            if peak_start is not None else "—"
        )
 
        # KPI summary of the three answers
        for col, label, value, sub in zip(
            st.columns(3),
            ["Peak Day", "Top Machine", "Peak Interval"],
            [top_day_fmt, top_machine_name, interval_fmt],
            [f"{top_day_val:,.1f} kW total", f"{top_machine_val:,.1f} kW", f"{peak_val:,.2f} kW" if peak_val else ""],
        ):
            col.markdown(f"""
            <div class="metric-card">
                <h3>{label}</h3>
                <p>{value}<br><span class="metric-sub">{sub}</span></p>
            </div>""", unsafe_allow_html=True)
 
        st.markdown("---")
        st.markdown("#### Daily total consumption")
        st.plotly_chart(fig_daily_totals(df_daily, top_day), use_container_width=True)

        st.markdown("---")
        st.markdown(f"#### Per-machine breakdown on {top_day_fmt}")
        st.plotly_chart(fig_machine_breakdown(df_machines), use_container_width=True)

        st.markdown("---")
        st.markdown(f"#### {top_machine_name}: consumption on {top_day_fmt}")
        if df_ts.empty:
            st.warning("No readings for this machine on the peak day.")
        else:
            st.plotly_chart(
                fig_machine_timeseries(df_ts, peak_start, peak_end, peak_val),
                use_container_width=True,
            )
 
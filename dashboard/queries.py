import pandas as pd
import streamlit as st
import duckdb
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from scripts.minio_utils import get_duckdb_s3_setup_sql

GOLD_BUCKET = os.environ.get("BUCKET_GOLD", "gold")

@st.cache_resource
def get_con():
    con = duckdb.connect()
    con.execute(get_duckdb_s3_setup_sql())
    con.execute(f"""
        CREATE OR REPLACE VIEW fact_measurement AS
        SELECT * FROM read_parquet(
            's3://{GOLD_BUCKET}/fact_measurement/**/*.parquet',
            hive_partitioning = false, union_by_name = true
        )
    """)

     # H71-specific gold table: hall_id, meter_id, timestamp, avg (no pre-aggregated max)
    con.execute(f"""
        CREATE OR REPLACE VIEW fact_measurement_h71 AS
        SELECT * FROM read_parquet(
            's3://{GOLD_BUCKET}/fact_measurement_h71/**/*.parquet',
            hive_partitioning = false, union_by_name = true
        )
    """)

    con.execute(f"""
        CREATE OR REPLACE VIEW dim_meter AS
        SELECT * FROM read_parquet(
            's3://{GOLD_BUCKET}/dim_meter/**/*.parquet',
            hive_partitioning = false, union_by_name = true
        )
    """)
    return con


@st.cache_data(ttl=300, show_spinner=False)
def query_overview(start, end, halls, trunc) -> pd.DataFrame:
    hf  = ", ".join(f"'{h}'" for h in halls)
    sql = f"""
        SELECT
            DATE_TRUNC('{trunc}', timestamp) AS bucket,
            SUM(avg)  AS total_consumption,
            MAX("max") AS peak_max
        FROM fact_measurement
        WHERE avg > 0 AND timestamp BETWEEN ? AND ? AND hall_id IN ({hf})
        GROUP BY DATE_TRUNC('{trunc}', timestamp)
        ORDER BY bucket
    """
    df = get_con().execute(sql, [start, end]).df()
    df["bucket"] = pd.to_datetime(df["bucket"])
    return df


@st.cache_data(ttl=300, show_spinner=False)
def query_by_hall(start, end, halls) -> pd.DataFrame:
    hf  = ", ".join(f"'{h}'" for h in halls)
    sql = f"""
        SELECT
            hall_id,
            DATE_TRUNC('hour', timestamp) AS bucket,
            SUM(avg) AS total_consumption
        FROM fact_measurement
        WHERE avg > 0 AND timestamp BETWEEN ? AND ? AND hall_id IN ({hf})
        GROUP BY hall_id, DATE_TRUNC('hour', timestamp)
        ORDER BY bucket, hall_id
    """
    df = get_con().execute(sql, [start, end]).df()
    df["bucket"] = pd.to_datetime(df["bucket"])
    return df


@st.cache_data(ttl=300, show_spinner=False)
def query_heatmap(start, end, halls) -> pd.DataFrame:
    hf  = ", ".join(f"'{h}'" for h in halls)
    sql = f"""
        SELECT
            hall_id,
            EXTRACT(HOUR FROM timestamp) AS hour_of_day,
            AVG(avg)                    AS avg_consumption
        FROM fact_measurement
        WHERE avg > 0 AND timestamp BETWEEN ? AND ? AND hall_id IN ({hf})
        GROUP BY hall_id, EXTRACT(HOUR FROM timestamp)
        ORDER BY hall_id, hour_of_day
    """
    return get_con().execute(sql, [start, end]).df()


@st.cache_data(ttl=300, show_spinner=False)
def query_daily_peak(start, end, halls) -> pd.DataFrame:
    hf  = ", ".join(f"'{h}'" for h in halls)
    sql = f"""
        WITH agg AS (
            SELECT
                hall_id,
                timestamp::DATE AS day,
                SUM(avg)       AS daily_total,
                MAX("max")      AS daily_peak
            FROM fact_measurement
            WHERE avg > 0 AND timestamp BETWEEN ? AND ?
              AND timestamp::DATE < CURRENT_DATE AND hall_id IN ({hf})
            GROUP BY hall_id, timestamp::DATE
        )
        SELECT *, RANK() OVER (PARTITION BY day ORDER BY daily_total DESC) AS rank_on_day
        FROM agg
        ORDER BY day, rank_on_day
    """
    df = get_con().execute(sql, [start, end]).df()
    df["day"] = pd.to_datetime(df["day"])
    return df


'''@st.cache_data(ttl=300, show_spinner=False)
def query_hall_drilldown(hall, peak_start, peak_end) -> pd.DataFrame:
    sql = """
        SELECT
            f.meter_id,
            COALESCE(d.meter_name, f.meter_id) AS meter_name,
            DATE_TRUNC('hour', f.timestamp)         AS hour_bucket,
            SUM(f.avg)                             AS total_consumption,
            MAX(f."max")                            AS peak_max
        FROM fact_measurement f
        LEFT JOIN dim_meter d ON f.meter_id = d.meter_id
        WHERE f.hall_id = ? AND f.avg > 0 AND f.timestamp BETWEEN ? AND ?
        GROUP BY f.meter_id, d.meter_name, DATE_TRUNC('hour', f.timestamp)
        ORDER BY hour_bucket, f.meter_id
    """
    df = get_con().execute(sql, [hall, peak_start, peak_end]).df()
    df["hour_bucket"] = pd.to_datetime(df["hour_bucket"]).astype(str)
    return df'''

@st.cache_data(ttl=300, show_spinner=False)
def query_hall_drilldown(hall, peak_start, peak_end) -> pd.DataFrame:
    sql = """
        SELECT
            f.meter_id,
            COALESCE(d.meter_name, f.meter_id) AS meter_name,
            DATE_TRUNC('minute', f.timestamp)  AS bucket,
            SUM(f.avg)                         AS total_consumption,
            MAX(f."max")                       AS peak_max
        FROM fact_measurement f
        LEFT JOIN dim_meter d ON f.meter_id = d.meter_id
        WHERE f.hall_id = ? AND f.avg > 0 AND f.timestamp BETWEEN ? AND ?
        GROUP BY f.meter_id, d.meter_name, DATE_TRUNC('minute', f.timestamp)
        ORDER BY bucket, f.meter_id
    """
    df = get_con().execute(sql, [hall, peak_start, peak_end]).df()
    df["bucket"] = pd.to_datetime(df["bucket"])
    return df



@st.cache_data(ttl=300, show_spinner=False)
def query_meter_drilldown(meter_id: str, peak_start: str, peak_end: str) -> pd.DataFrame:
    sql = """
        SELECT
            DATE_TRUNC('minute', f.timestamp) AS timestamp,
            f.meter_id,
            AVG(f.avg)   AS consumption,
            MAX(f."max") AS peak_max
        FROM fact_measurement f
        WHERE f.meter_id = ?
          AND f.avg > 0
          AND f.timestamp BETWEEN ? AND ?
        GROUP BY DATE_TRUNC('minute', f.timestamp), f.meter_id
        ORDER BY timestamp
    """
    df = get_con().execute(sql, [meter_id, peak_start, peak_end]).df()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df




# H71 THREE-STEP DRILL-DOWN
# Step 1: which day had the highest total consumption for H71 (whole year)?
# Step 2: which machine (meter_id) contributed most on that day?
# Step 3: which time interval was the peak for that machine on that day?
 
'''@st.cache_data(ttl=300, show_spinner=False)
def query_h71_daily_totals() -> pd.DataFrame:
    """Total consumption per day for Hall 71, across the full year."""
    sql = """
        SELECT
            timestamp::DATE AS day,
            SUM(avg)        AS daily_total
        FROM fact_measurement_h71
        WHERE avg > 0
        GROUP BY timestamp::DATE
        ORDER BY day
    """
    df = get_con().execute(sql).df()
    df["day"] = pd.to_datetime(df["day"])
    return df'''

@st.cache_data(ttl=300, show_spinner=False)
def query_h71_daily_totals() -> pd.DataFrame:
    """Per-day, per-machine total consumption for Hall 71, across the full
    year. Kept at machine granularity so the chart can stack by machine,
    same as the hall drill-down view."""
    sql = """
        SELECT
            f.timestamp::DATE                  AS day,
            f.meter_id,
            COALESCE(d.meter_name, f.meter_id) AS meter_name,
            SUM(f.avg)                         AS daily_total
        FROM fact_measurement_h71 f
        LEFT JOIN dim_meter d ON f.meter_id = d.meter_id
        WHERE f.avg > 0
        GROUP BY f.timestamp::DATE, f.meter_id, d.meter_name
        ORDER BY day
    """
    df = get_con().execute(sql).df()
    df["day"] = pd.to_datetime(df["day"])
    return df
 
@st.cache_data(ttl=300, show_spinner=False)
def query_h71_top_day() -> tuple[str, float]:
    """Step 1 - Returns (day, daily_total) for the single highest-consumption day."""
    sql = """
        SELECT
            timestamp::DATE AS day,
            SUM(avg)        AS daily_total
        FROM fact_measurement_h71
        WHERE avg > 0
        GROUP BY timestamp::DATE
        ORDER BY daily_total DESC
        LIMIT 1
    """
    row = get_con().execute(sql).df().iloc[0]
    return str(row["day"]), float(row["daily_total"])
 
 
@st.cache_data(ttl=300, show_spinner=False)
def query_h71_machine_breakdown(day: str, top_n: int = 5) -> pd.DataFrame:
    """Step 2 - Top-N machines by total consumption for a single day, ranked descending."""
    sql = f"""
        SELECT
            f.meter_id,
            COALESCE(d.meter_name, f.meter_id) AS meter_name,
            SUM(f.avg)                         AS total_consumption
        FROM fact_measurement_h71 f
        LEFT JOIN dim_meter d ON f.meter_id = d.meter_id
        WHERE f.avg > 0 AND f.timestamp::DATE = ?
        GROUP BY f.meter_id, d.meter_name
        ORDER BY total_consumption DESC
        LIMIT {int(top_n)}
    """
    return get_con().execute(sql, [day]).df()
 
 
@st.cache_data(ttl=300, show_spinner=False)
def query_h71_machine_timeseries(day: str, meter_id: str) -> pd.DataFrame:
    """Step 3 (data) - Full-resolution consumption series for one machine on one day."""
    sql = """
        SELECT
            f.timestamp AS timestamp,
            f.avg       AS consumption
        FROM fact_measurement_h71 f
        WHERE f.meter_id = ? AND f.avg > 0 AND f.timestamp::DATE = ?
        ORDER BY f.timestamp
    """
    df = get_con().execute(sql, [meter_id, day]).df()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df
 
 
def find_peak_interval(df_ts: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp, float]:
    """Step 3 (answer) - Given a machine's time series for one day, return
    (interval_start, interval_end, peak_value) for the reading with the
    highest consumption. Interval width is inferred from the data's own
    sampling cadence (typically 15 min) rather than hard-coded."""
    if df_ts.empty:
        return None, None, None
    step = df_ts["timestamp"].diff().mode()
    step = step.iloc[0] if not step.empty else pd.Timedelta(minutes=15)
    peak_row = df_ts.loc[df_ts["consumption"].idxmax()]
    start = peak_row["timestamp"]
    return start, start + step, float(peak_row["consumption"])
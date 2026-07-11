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
    con.execute(f"""
        CREATE OR REPLACE VIEW dim_station AS
        SELECT * FROM read_parquet(
            's3://{GOLD_BUCKET}/dim_station/**/*.parquet',
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


@st.cache_data(ttl=300, show_spinner=False)
def query_hall_drilldown(hall, peak_start, peak_end) -> pd.DataFrame:
    sql = """
        SELECT
            f.station_id,
            COALESCE(d.station_name, f.station_id) AS station_name,
            DATE_TRUNC('hour', f.timestamp)         AS hour_bucket,
            SUM(f.avg)                             AS total_consumption,
            MAX(f."max")                            AS peak_max
        FROM fact_measurement f
        LEFT JOIN dim_station d ON f.station_id = d.station_id
        WHERE f.hall_id = ? AND f.avg > 0 AND f.timestamp BETWEEN ? AND ?
        GROUP BY f.station_id, d.station_name, DATE_TRUNC('hour', f.timestamp)
        ORDER BY hour_bucket, f.station_id
    """
    df = get_con().execute(sql, [hall, peak_start, peak_end]).df()
    df["hour_bucket"] = pd.to_datetime(df["hour_bucket"]).astype(str)
    return df

@st.cache_data(ttl=300, show_spinner=False)
def query_station_drilldown(station_id: str, peak_start: str, peak_end: str) -> pd.DataFrame:
    sql = """
        SELECT
            DATE_TRUNC('minute', f.timestamp) AS timestamp,
            f.meter_id,
            AVG(f.avg)   AS consumption,
            MAX(f."max") AS peak_max
        FROM fact_measurement f
        WHERE f.station_id = ?
          AND f.avg > 0
          AND f.timestamp BETWEEN ? AND ?
        GROUP BY DATE_TRUNC('minute', f.timestamp), f.meter_id
        ORDER BY timestamp
    """
    df = get_con().execute(sql, [station_id, peak_start, peak_end]).df()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df
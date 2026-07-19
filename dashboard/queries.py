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
        CREATE OR REPLACE VIEW dim_meter AS
        SELECT * FROM read_parquet(
            's3://{GOLD_BUCKET}/dim_meter/**/*.parquet',
            hive_partitioning = false, union_by_name = true
        )
    """)

    con.execute(f"""
        CREATE OR REPLACE VIEW dim_hall AS
        SELECT * FROM read_parquet(
            's3://{GOLD_BUCKET}/dim_hall/**/*.parquet',
            hive_partitioning = false, union_by_name = true
        )
    """)
    return con


@st.cache_data(ttl=300, show_spinner=False)
def query_halls() -> list[str]:
    sql = """
        SELECT DISTINCT hall_id
        FROM dim_hall
        WHERE hall_id IS NOT NULL
        ORDER BY hall_id
    """
    return get_con().execute(sql).df()["hall_id"].tolist()


# THREE-STEP DRILL-DOWN (any hall, any date range)
# Step 1: which day in the range had the highest total consumption for the selected hall?
# Step 2: which machine (meter_id) contributed most on that day?
# Step 3: which time interval was the peak for that machine on that day?

@st.cache_data(ttl=300, show_spinner=False)
def query_daily_totals(hall: str, start: str, end: str) -> pd.DataFrame:
    sql = """
        SELECT
            f.timestamp::DATE                  AS day,
            f.meter_id,
            COALESCE(d.meter_name, f.meter_id) AS meter_name,
            SUM(f.value)                       AS daily_total
        FROM fact_measurement f
        LEFT JOIN dim_meter d ON f.meter_id = d.meter_id
        WHERE f.hall_id = ? AND f.value > 0 AND f.timestamp BETWEEN ? AND ?
        GROUP BY f.timestamp::DATE, f.meter_id, d.meter_name
        ORDER BY day
    """
    df = get_con().execute(sql, [hall, start, end]).df()
    df["day"] = pd.to_datetime(df["day"])
    return df


@st.cache_data(ttl=300, show_spinner=False)
def query_top_day(hall: str, start: str, end: str) -> tuple[str, float]:
    sql = """
        SELECT
            timestamp::DATE AS day,
            SUM(value)      AS daily_total
        FROM fact_measurement
        WHERE hall_id = ? AND value > 0 AND timestamp BETWEEN ? AND ?
        GROUP BY timestamp::DATE
        ORDER BY daily_total DESC
        LIMIT 1
    """
    result = get_con().execute(sql, [hall, start, end]).df()
    if result.empty:
        return None, None
    row = result.iloc[0]
    return str(row["day"]), float(row["daily_total"])


@st.cache_data(ttl=300, show_spinner=False)
def query_machine_breakdown(hall: str, day: str, top_n: int = 5) -> pd.DataFrame:
    sql = f"""
        SELECT
            f.meter_id,
            COALESCE(d.meter_name, f.meter_id) AS meter_name,
            SUM(f.value)                       AS total_consumption
        FROM fact_measurement f
        LEFT JOIN dim_meter d ON f.meter_id = d.meter_id
        WHERE f.hall_id = ? AND f.value > 0 AND f.timestamp::DATE = ?
        GROUP BY f.meter_id, d.meter_name
        ORDER BY total_consumption DESC
        LIMIT {int(top_n)}
    """
    return get_con().execute(sql, [hall, day]).df()


@st.cache_data(ttl=300, show_spinner=False)
def query_machine_timeseries(hall: str, meter_id: str, day: str) -> pd.DataFrame:
    sql = """
        SELECT
            f.timestamp AS timestamp,
            f.value     AS consumption
        FROM fact_measurement f
        WHERE f.hall_id = ? AND f.meter_id = ? AND f.value > 0 AND f.timestamp::DATE = ?
        ORDER BY f.timestamp
    """
    df = get_con().execute(sql, [hall, meter_id, day]).df()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def find_peak_interval(df_ts: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp, float]:
    if df_ts.empty:
        return None, None, None
    step = df_ts["timestamp"].diff().mode()
    step = step.iloc[0] if not step.empty else pd.Timedelta(minutes=15)
    peak_row = df_ts.loc[df_ts["consumption"].idxmax()]
    start = peak_row["timestamp"]
    return start, start + step, float(peak_row["consumption"])
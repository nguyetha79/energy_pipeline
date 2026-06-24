"""
  1. Reads ALL cleaned hall Parquet files at once (DuckDB can read a
     whole folder of Parquet files with a glob pattern like
     's3://clean/H*/data.parquet').

  2. Builds 3 aggregation tables:
       - 15-minute level   (basically the raw data, lightly aggregated)
       - hourly level
       - daily level
     Each aggregation is produced at THREE levels:
       - per station
       - per hall
       - company-wide (all halls combined)

  3. Adds a PEAK FLAG:
       A row is flagged as a "peak" if its value exceeds
       (rolling mean + 2 * rolling standard deviation) over a
       rolling window of previous observations.

  4. Adds a COST PERIOD LABEL:
       - "peak"     -> weekdays, 08:00-20:00 (typical high-tariff window)
       - "off_peak" -> nights and weekends
     (These hours are simple defaults for an academic project and can
     easily be changed in one place - see PEAK_HOUR_START / END below.)

  5. Writes the results as Parquet files into the 'gold' bucket,
     partitioned by hall_id, ready for the Streamlit dashboard.
"""

import os
import io
import duckdb
import pandas as pd

from minio_utils import (
    get_s3_client,
    ensure_bucket_exists,
    upload_bytes,
    get_duckdb_s3_setup_sql,
)

SILVER_BUCKET = os.environ.get("BUCKET_SILVER", "silver")
GOLD_BUCKET = os.environ.get("BUCKET_GOLD", "gold")

PEAK_HOUR_START = 8   # 08:00
PEAK_HOUR_END = 20    # 20:00
ROLLING_WINDOW = 20   # number of points used for rolling mean/std (peak detection)
PEAK_STD_MULTIPLIER = 2  # "2 sigma" rule from the project plan


def get_connection():
    """
    Create a DuckDB connection configured to read/write Parquet files
    directly from MinIO (S3-compatible storage).
    """
    con = duckdb.connect()
    con.execute(get_duckdb_s3_setup_sql())
    return con


def build_aggregation_sql(time_bucket_sql, level):
    """
    Build a SQL query that:
      - reads the cleaned data
      - groups it into the given time bucket (15min / hour / day)
      - at the given level (station / hall / company)
      - computes total, average, and peak(max) consumption
      - flags rolling-window peaks
      - adds a cost-period label

    Parameters:
        time_bucket_sql : SQL expression that truncates the timestamp,
                          e.g. "timestamp" (15min, no truncation needed
                          since source data is already 15min),
                          "date_trunc('hour', timestamp)",
                          "date_trunc('day', timestamp)"
        level           : 'station', 'hall', or 'company'
    """

    # Decide the GROUP BY columns depending on the aggregation level
    if level == "station":
        group_cols = "hall_id, station_id, station_name"
    elif level == "hall":
        group_cols = "hall_id"
    else:  # company
        group_cols = "'ALL' as hall_id"

    select_group_cols = group_cols if level != "company" else "'ALL' as hall_id"

    sql = f"""
    WITH base AS (
        SELECT
            hall_id,
            station_id,
            station_name,
            {time_bucket_sql} AS ts_bucket,
            wert
        FROM clean_data
    ),
    aggregated AS (
        SELECT
            {select_group_cols}{',' if level == 'station' else ''}
            {'station_id, station_name,' if level == 'station' else ''}
            ts_bucket,
            SUM(wert)   AS total_consumption,
            AVG(wert)   AS avg_consumption,
            MAX(wert)   AS max_consumption,
            COUNT(*)    AS num_readings
        FROM base
        GROUP BY {select_group_cols}{', station_id, station_name' if level == 'station' else ''}, ts_bucket
    ),
    with_rolling AS (
        SELECT
            *,
            AVG(total_consumption) OVER (
                {'PARTITION BY hall_id, station_id' if level == 'station' else ('PARTITION BY hall_id' if level == 'hall' else '')}
                ORDER BY ts_bucket
                ROWS BETWEEN {ROLLING_WINDOW} PRECEDING AND 1 PRECEDING
            ) AS rolling_mean,
            STDDEV(total_consumption) OVER (
                {'PARTITION BY hall_id, station_id' if level == 'station' else ('PARTITION BY hall_id' if level == 'hall' else '')}
                ORDER BY ts_bucket
                ROWS BETWEEN {ROLLING_WINDOW} PRECEDING AND 1 PRECEDING
            ) AS rolling_std
        FROM aggregated
    )
    SELECT
        *,
        CASE
            WHEN rolling_mean IS NOT NULL
                 AND rolling_std IS NOT NULL
                 AND total_consumption > (rolling_mean + {PEAK_STD_MULTIPLIER} * rolling_std)
            THEN TRUE
            ELSE FALSE
        END AS is_peak,
        CASE
            WHEN EXTRACT(dow FROM ts_bucket) IN (0, 6) THEN 'off_peak'  -- weekend
            WHEN EXTRACT(hour FROM ts_bucket) >= {PEAK_HOUR_START}
                 AND EXTRACT(hour FROM ts_bucket) < {PEAK_HOUR_END} THEN 'peak'
            ELSE 'off_peak'
        END AS cost_period
    FROM with_rolling
    ORDER BY ts_bucket
    """
    return sql


def run(**context):
    """
    Main entry point, called by the Airflow PythonOperator.

    For each combination of:
        time grain  -> 15min, hourly, daily
        level       -> station, hall, company
    runs a DuckDB query and writes the result to the gold bucket.
    """
    s3_client = get_s3_client()
    ensure_bucket_exists(s3_client, GOLD_BUCKET)

    con = get_connection()

    # Load ALL cleaned hall files into a DuckDB view called 'clean_data'.
    # The glob pattern '*' matches every hall_id=... folder.
    con.execute(f"""
        CREATE OR REPLACE VIEW clean_data AS
        SELECT * FROM read_parquet('s3://{SILVER_BUCKET}/H*/data.parquet', hive_partitioning=1)
    """)

    row_count = con.execute("SELECT COUNT(*) FROM clean_data").fetchone()[0]
    print(f"Loaded {row_count} rows from clean zone.")

    if row_count == 0:
        print("No data found in clean zone - skipping gold aggregation.")
        return []

    # Time-bucket SQL expressions for each grain
    time_grains = {
        "15min": "timestamp",  # source data is already at 15-minute resolution
        "hourly": "date_trunc('hour', timestamp)",
        "daily": "date_trunc('day', timestamp)",
    }

    levels = ["station", "hall", "company"]

    written_keys = []

    for grain_name, time_bucket_sql in time_grains.items():
        for level in levels:
            sql = build_aggregation_sql(time_bucket_sql, level)
            df = con.execute(sql).fetchdf()

            if df.empty:
                print(f"  [{grain_name}/{level}] -> no data, skipping")
                continue

            # Write one Parquet file per (grain, level), partitioned by hall_id
            # by writing separate files for each hall_id value.
            for hall_id, hall_df in df.groupby("hall_id"):
                buffer = io.BytesIO()
                hall_df.to_parquet(buffer, index=False)
                buffer.seek(0)

                key = f"{grain_name}/{level}/hall_id={hall_id}/data.parquet"
                upload_bytes(s3_client, GOLD_BUCKET, key, buffer.read())
                written_keys.append(key)
                print(f"  [{grain_name}/{level}] -> {key} ({len(hall_df)} rows)")

    print(f"Done. {len(written_keys)} gold files written.")
    return written_keys


if __name__ == "__main__":
    run()

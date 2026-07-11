"""
  1. Reads ALL cleaned hall Parquet files at once (DuckDB can read a
     whole folder of Parquet files with a glob pattern like
     's3://clean/H*/data.parquet').

  2. Builds fact & dim tables
    - fact_measurement
    - dim_hall
    - dim_station
    - dim_meter

  3. Writes the results as Parquet files into the 'gold' bucket,
     ready for the Streamlit dashboard.
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

def get_connection():
    """
    Create a DuckDB connection configured to read/write Parquet files
    directly from MinIO (S3-compatible storage).
    """
    con = duckdb.connect()
    con.execute(get_duckdb_s3_setup_sql())
    return con

def run(**context):
    s3_client = get_s3_client()
    ensure_bucket_exists(s3_client, GOLD_BUCKET)

    con = get_connection()

    # Read all silver parquet files
    con.execute(f"""
        CREATE OR REPLACE VIEW total_energy_data AS
        SELECT *
        FROM read_parquet(
            's3://{SILVER_BUCKET}/H*/data.parquet',
            hive_partitioning=1
        )
    """)

    row_count = con.execute(
        "SELECT COUNT(*) FROM total_energy_data"
    ).fetchone()[0]

    print(f"Loaded {row_count} rows from silver layer.")

    if row_count == 0:
        print("No data found.")
        return []

    # Create Gold Star Schema

    con.execute("""
        CREATE OR REPLACE TABLE fact_measurement AS
        SELECT
            hall_id,
            meter_id,
            station_id,
            timestamp,
            min,
            max,
            avg
        FROM total_energy_data
    """)

    con.execute("""
        CREATE OR REPLACE TABLE dim_hall AS
        SELECT DISTINCT
            hall_id,
            hall_label
        FROM total_energy_data
    """)

    con.execute("""
        CREATE OR REPLACE TABLE dim_station AS
        SELECT DISTINCT
            hall_id,
            station_id,
            station_name,
            station_desc
        FROM total_energy_data
    """)

    con.execute("""
        CREATE OR REPLACE TABLE dim_meter AS
        SELECT DISTINCT
            station_id,    
            meter_id,
            interval_minutes
        FROM total_energy_data
    """)

    tables = [
        "fact_measurement",
        "dim_hall",
        "dim_station",
        "dim_meter",
    ]

    written_keys = []

    for table in tables:
        df = con.execute(f"SELECT * FROM {table}").fetchdf()

        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False)
        buffer.seek(0)

        key = f"{table}/data.parquet"

        upload_bytes(
            s3_client,
            GOLD_BUCKET,
            key,
            buffer.read()
        )

        written_keys.append(key)
        print(f"Wrote {key} ({len(df)} rows)")

    return written_keys

if __name__ == "__main__":
    run()

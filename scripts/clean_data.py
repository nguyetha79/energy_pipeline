"""
  1. Reads all raw Parquet files (one per meter/sheet) from the
     'raw' bucket using DuckDB.
  2. Renames columns into the unified schema described in the plan:
       hall_id, meter_id, timestamp, value
  3. Converts the "Zeitbereich" timestamp column into a real UTC
     timestamp.
  4. Handles missing values by keeping rows with a valid timestamp,
     and replacing missing metric values (min/max/avg) with 0.
  5. Writes ONE combined Parquet file per hall into the 'clean' bucket,
     partitioned by hall_id.
"""

import os
import io
import duckdb
import pandas as pd

from minio_utils import (
    get_s3_client,
    ensure_bucket_exists,
    upload_bytes,
    list_objects,
    download_bytes,
    get_duckdb_s3_setup_sql,
)

BRONZE_BUCKET = os.environ.get("BUCKET_BRONZE", "bronze")
SILVER_BUCKET = os.environ.get("BUCKET_SILVER", "silver")


def normalize_hall_dataframe(df, hall_id=None):
    """
    Take a raw DataFrame from the bronze layer and return
    a normalized DataFrame matching the unified schema:

        hall_id, hall_name, meter_id, meter_name, meter_desc,
        timestamp (UTC), value, src_file

    Steps performed:
      - Rename 'MIN' / 'MAX' / 'MAX (AVG)' columns to the
        unified lowercase names.
      - Parse the 'Zeitbereich' (date) column into a proper datetime
        and treat it as UTC
      - Keep rows with a valid timestamp.
      - Preserve meter_id and meter_desc if present.
      - Replace missing value with 0.
    """
    df = df.copy()
    df["hall_id"] = hall_id

    # Rename measurement columns to the unified schema
    # The exact column names can vary slightly between files, so we
    # search for the right column rather than assuming a fixed name.
    rename_map = {}
    for col in df.columns:
        col_clean = str(col).strip().lower()
        if col_clean == "zeitbereich":
            rename_map[col] = "timestamp_raw"
        elif col_clean == "wert":
            rename_map[col] = "value"

    df = df.rename(columns=rename_map)

    if "timestamp_raw" not in df.columns:
        timestamp_candidates = [
            col for col in df.columns
            if any(token in str(col).strip().lower() for token in ("zeit", "time", "date", "timestamp"))
        ]
        if timestamp_candidates:
            df = df.rename(columns={timestamp_candidates[0]: "timestamp_raw"})
        else:
            df["timestamp_raw"] = pd.NaT

    # Parse timestamp into UTC 
    # dayfirst=True because the source format is DD.MM.YYYY (German format)
    df["timestamp"] = pd.to_datetime(df["timestamp_raw"], dayfirst=True, errors="coerce", utc=True)

    # Keep rows with a valid timestamp, fill missing metrics with 0 
    before = len(df)
    df = df[df["timestamp"].notna()].copy()
    after = len(df)
    if before != after:
        print(f"  Dropped {before - after} rows with missing timestamp")

    # Keep only the unified columns 
    final_cols = [
        "hall_id", "hall_label", "meter_id",
        "meter_name", "meter_desc",
        "timestamp", "value",
        "interval_minutes", "src_file",
    ]
    for col in final_cols:
        if col not in df.columns:
            df[col] = pd.NA

    return df[final_cols]


def run(**context):
    """
    Main entry point, called by the Airflow PythonOperator.

    For each hall (hall_id), combine all of its raw meter Parquet
    files into a single normalized Parquet file in the 'clean' bucket.
    """
    s3_client = get_s3_client()
    ensure_bucket_exists(s3_client, BRONZE_BUCKET)
    ensure_bucket_exists(s3_client, SILVER_BUCKET)

    raw_keys = list_objects(s3_client, BRONZE_BUCKET)
    parquet_keys = [k for k in raw_keys if k.endswith(".parquet")]

    if not parquet_keys:
        print("No bronze Parquet files found - nothing to clean.")
        return []

    # Group parquet keys by hall_id (the folder prefix, e.g. 'H01/...')
    halls = {}
    for key in parquet_keys:
        hall_id = key.split("/")[0]
        halls.setdefault(hall_id, []).append(key)

    written_keys = []

    for hall_id, keys in halls.items():
        print(f"Cleaning hall {hall_id} ({len(keys)} meter files)...")

        frames = []
        for key in keys:
            raw_bytes = download_bytes(s3_client, BRONZE_BUCKET, key)
            df = pd.read_parquet(io.BytesIO(raw_bytes))
            normalized = normalize_hall_dataframe(df, hall_id=hall_id)
            frames.append(normalized)

        hall_df = pd.concat(frames, ignore_index=True)

        # Write the cleaned hall data as one Parquet file
        buffer = io.BytesIO()
        hall_df.to_parquet(buffer, index=False)
        buffer.seek(0)

        clean_key = f"{hall_id}/data.parquet"
        upload_bytes(s3_client, SILVER_BUCKET, clean_key, buffer.read())
        written_keys.append(clean_key)

        print(f"  -> {clean_key} ({len(hall_df)} rows)")

    print(f"Done. {len(written_keys)} clean files written.")
    return written_keys


if __name__ == "__main__":
    run()
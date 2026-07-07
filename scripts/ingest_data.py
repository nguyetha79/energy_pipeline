"""
  1. Reads every .xlsx file from the local 'data/input' folder.
  2. Uploads the ORIGINAL, untouched Excel file to the 'raw' bucket
     (so we always keep a copy of the source data).
  3. Opens each worksheet (one worksheet = one measurement station)
     using openpyxl, because the files have a non-standard layout:

        Row 1: "Export der Messwerte   16.09.2025 16:30:44"
        Row 2: "570   Z 1130 Lade...    Ladesäulen Fremdfahrzeuge P1"
        Row 3: "Zeitbereich   Wirkleistung Summe L1..L3 [15m]"
        Row 4: "ID (Entfernen) Zeitbereich  Wert  MIN  MAX  MAX(AVG)"   <- real header
        Row 5+: actual data rows

     pandas.read_excel() cannot handle this because it expects the
     header to be in row 1. So we use openpyxl to read cell-by-cell,
     skip the metadata rows, and build a clean DataFrame ourselves.

  4. Saves each worksheet as its own Parquet file in the 'raw' bucket
     (one Parquet file per station), keeping the data EXACTLY as
     extracted (no cleaning yet - that happens in transform_clean.py).

WHY KEEP BOTH EXCEL AND PARQUET IN THE RAW ZONE?
  - The Excel file is the "source of truth" - if something goes wrong
    later, we can always re-process from here.
  - The Parquet version is just a faster, structured copy of the same
    data, used as input for the next pipeline stage.
"""

import os
import io
import re

import numpy as np
import pandas as pd
import openpyxl

from minio_utils import get_s3_client, ensure_bucket_exists, upload_bytes, upload_file

# Prefer the mounted project folder when running on the host, but keep the
# container path for Airflow execution inside Docker.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_INPUT_DIR = os.path.join(PROJECT_ROOT, "data", "input")
CONTAINER_INPUT_DIR = "/opt/airflow/data/input"
INPUT_DIR = DEFAULT_INPUT_DIR if os.path.isdir(DEFAULT_INPUT_DIR) else CONTAINER_INPUT_DIR

BRONZE_BUCKET = os.environ.get("BUCKET_BRONZE") or "bronze"


    

def extract_sheet_metadata(worksheet):
    sheet_name = worksheet.title

    # Fallback parsing from row 2/3 if the cells are blank.
    row2_values = [cell.value for cell in worksheet[2]]
    meter_id = str(int(row2_values[0])) if isinstance(row2_values[0], (int, float)) else None
    
    row2_strings = [v for v in row2_values if isinstance(v, str) and v.strip()]
    station_name = row2_strings[0] if row2_strings else sheet_name
    station_desc = row2_strings[1] if len(row2_strings) >= 2 else ""

    row3_values = [cell.value for cell in worksheet[3]]
    row3_strings = [v for v in row3_values if isinstance(v, str) and v.strip()]

    if any(v.strip().lower() == "zeitbereich" for v in row3_strings):
        worksheet["A4"].value = "Zeitbereich"

    interval_label = next(
        (v for v in row3_strings if re.search(r"\[\d+\s*m\]", v)),
        None
    )
    if interval_label is None:
        interval_label = row3_strings[-1] if row3_strings else ""

    return {
        "sheet_name": sheet_name,
        "meter_id": meter_id,
        "station_name": station_name,
        "station_desc": station_desc,
        "interval_label": interval_label,
        "header_row_index": 4,
    }


def worksheet_to_dataframe(worksheet, header_row_index):
    """
    Convert a worksheet into a pandas DataFrame, using the given row
    number as the header row, and everything below it as data.

    We do this manually (instead of pd.read_excel) because the header
    is not in row 1.
    """
    data_rows = []
    header_values = None

    for i, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
        if i < header_row_index:
            continue  # skip metadata rows above the header
        if i == header_row_index:
            # Clean header names: turn None into a placeholder, strip spaces
            header_values = [
                str(v).strip() if v is not None else f"col_{j}"
                for j, v in enumerate(row)
            ]
            continue
        # Stop if we hit a completely empty row (end of data)
        if all(v is None for v in row):
            continue
        data_rows.append(row)

    df = pd.DataFrame(data_rows, columns=header_values)
    return df


def extract_interval_minutes(interval_label):
    """
    Try to read something like '[15m]' out of the interval label
    and return the number of minutes as an integer (e.g. 15).
    Defaults to 15 if nothing is found, since that is the format
    used across all files in this project.
    """
    match = re.search(r"\[(\d+)\s*m\]", str(interval_label))
    if match:
        return int(match.group(1))
    return 15

def normalize_dataframe(df):
    """
    Clean Excel-style placeholder values like '#N/A' before Parquet export.

    PyArrow is strict about mixed object columns; replacing invalid markers
    with real NaNs and coercing numeric-looking columns avoids the ArrowInvalid
    exceptions seen with files containing '#N/A' in measurement columns.
    """
    cleaned = df.copy()

    for column in cleaned.columns:
        series = cleaned[column]

        if not pd.api.types.is_object_dtype(series.dtype):
            continue

        normalized = series.astype("string").replace({
            "#N/A": np.nan,
            "#N/A.NA": np.nan,
            "N/A": np.nan,
            "NA": np.nan,
            "NaN": np.nan,
            "None": np.nan,
            "": np.nan,
        })

        # Keep string columns intact unless the values are mostly numeric-like.
        numeric_candidate = pd.to_numeric(normalized, errors="coerce")
        non_null_original = normalized.notna().sum()
        numeric_non_null = numeric_candidate.notna().sum()

        if non_null_original and numeric_non_null >= max(1, int(0.8 * non_null_original)):
            cleaned[column] = numeric_candidate
        else:
            # Preserve strings but convert placeholder values to real nulls.
            cleaned[column] = normalized.astype("string")

    return cleaned


def process_excel_file(local_file_path, hall_id, hall_label, s3_client):
    """
    Process a single Excel file:
      - upload the original file to raw/<hall_id>/<filename>.xlsx
      - for each worksheet, extract data + metadata
      - upload one Parquet file per worksheet to
        raw/<hall_id>/<station_name_safe>.parquet
    """
    file_name = os.path.basename(local_file_path)

    # 1) Upload the ORIGINAL Excel file, unchanged
    raw_excel_key = f"{hall_id}/{file_name}"
    upload_file(s3_client, BRONZE_BUCKET, raw_excel_key, local_file_path)

    # 2) Open with openpyxl (data_only=True returns calculated values, not formulas)
    workbook = openpyxl.load_workbook(local_file_path, data_only=True)

    parquet_keys = []

    for worksheet in workbook.worksheets:
        if hall_id == "H1":
            worksheet.delete_cols(1)

        meta = extract_sheet_metadata(worksheet)
        safe_sheet_name = re.sub(r"[^A-Za-z0-9_]+", "_", meta["sheet_name"]).strip("_")
        station_id = f"{hall_id}_{safe_sheet_name}".lower()

        df = worksheet_to_dataframe(worksheet, meta["header_row_index"])
        df = normalize_dataframe(df)

        if df.empty:
            print(f"  Skipping empty sheet: {meta['sheet_name']}")
            continue

        # Add columns that record WHERE this data came from.
        # This is important for traceability and for the next pipeline stages.
        df["hall_id"] = hall_id
        df["hall_label"] = hall_label
        df["meter_id"] = meta.get("meter_id")
        df["station_id"] = station_id
        df["station_name"] = meta.get("station_name") or meta["sheet_name"]
        df["station_desc"] = meta.get("station_desc") or ""
        df["interval_minutes"] = extract_interval_minutes(meta["interval_label"])
        df["src_file"] = file_name

        # Build a safe filename from the sheet name (replace spaces etc.)
        safe_sheet_name = re.sub(r"[^A-Za-z0-9_]+", "_", meta["sheet_name"]).strip("_")
        parquet_key = f"{hall_id}/{safe_sheet_name}.parquet"

        # Convert DataFrame -> Parquet bytes -> upload to MinIO
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False)
        buffer.seek(0)
        upload_bytes(s3_client, BRONZE_BUCKET, parquet_key, buffer.read())

        parquet_keys.append(parquet_key)
        print(f"  Sheet '{meta['sheet_name']}' -> {parquet_key} ({len(df)} rows)")

    return parquet_keys


def run(**context):
    """
    Main entry point, called by the Airflow PythonOperator.

    Loops over every .xlsx file in the input directory and processes it.
    The 'hall_id' is derived from the filename (e.g. H1, H2, ...).
    """
    s3_client = get_s3_client()
    ensure_bucket_exists(s3_client, BRONZE_BUCKET)

    if not os.path.isdir(INPUT_DIR):
        print(f"Input directory not found: {INPUT_DIR}")
        return []

    excel_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith(".xlsx")]

    if not excel_files:
        print("No Excel files found in input directory.")
        return []

    all_parquet_keys = []

    for file_name in sorted(excel_files):
        local_path = os.path.join(INPUT_DIR, file_name)

        # Derive a hall_id from the filename, e.g. "Export_Copilot_H1_....xlsx" -> "H1"
        match = re.search(r"(H\d+)", file_name)
        hall_id = match.group(1) if match else os.path.splitext(file_name)[0]
        hall_label = f"Hall {hall_id}"

        print(f"Processing file: {file_name} (hall_id={hall_id})")
        keys = process_excel_file(local_path, hall_id, hall_label, s3_client)
        all_parquet_keys.extend(keys)

    print(f"Done. {len(all_parquet_keys)} Parquet files written to raw zone.")
    return all_parquet_keys


if __name__ == "__main__":
    # Allows running this script directly for testing:
    run()
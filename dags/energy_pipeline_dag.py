'''"""
AIRFLOW DAG that orchestrates the energy consumption pipeline:

    1. Detect newly uploaded files   -> FileSensor
    2. Parse Excel -> Parquet (Bronze) -> ingest_data.py
    3. Transform & aggregate (Silver -> Gold) -> clean_data.py, derive_data.py
    4. Send completion notification  -> notify.py

HOW IT WORKS:
  - The FileSensor waits until at least one .xlsx file appears in the
    'data/input' folder. It checks every 30 seconds for up to 10 minutes
    (per run), using 'reschedule' mode so it doesn't hold a worker slot
    while waiting.
  - Once a file is found, the DAG runs the 4 stages IN ORDER. Each
    stage only runs if the previous one succeeded (default Airflow
    behaviour for tasks connected with '>>').
  - The DAG itself is scheduled to run once every 6 hours (see
    'schedule_interval' below). If no file shows up within the 10-minute
    sensing window, the run is marked as skipped (not failed) rather
    than alerting anyone - see 'soft_fail' on the sensor.
  - If ANY task in the DAG fails, 'notify_on_failure' fires regardless
    of which stage failed, so failures are never silent.
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.sensors.filesystem import FileSensor

# Make sure the 'scripts' folder is importable
sys.path.append("/opt/airflow/scripts")
ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from scripts import clean_data, derive_data, ingest_data, notify

logger = logging.getLogger(__name__)


def notify_on_failure(context):
    """
    Called automatically by Airflow if any task in the DAG fails.
    Reuses the existing notify.run() but passes failure context so the
    message can say *what* failed, not just that something did.
    """
    task_instance = context.get("task_instance")
    exception = context.get("exception")
    try:
        notify.run(
            status="FAILURE",
            failed_task=task_instance.task_id if task_instance else "unknown",
            error=str(exception) if exception else "unknown error",
        )
    except Exception:
        logger.exception("notify_on_failure: failed to send failure notification")


# Default arguments applied to every task in this DAG
default_args = {
    "owner": "data_engineering_team",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
    "on_failure_callback": notify_on_failure,
}


with DAG(
    dag_id="energy_pipeline_dag",
    description="Centralize, normalize, and aggregate energy consumption data from Excel files",
    default_args=default_args,
    start_date=datetime(2025, 1, 1),
    schedule_interval=timedelta(hours=24),
    catchup=False,                 # don't run for past missed schedules
    max_active_runs=1,             # never let two runs overlap
    tags=["energy", "medallion", "duckdb", "minio"],
) as dag:

    # STAGE 1: Detect newly uploaded Excel files.
    # mode="reschedule" frees the worker slot between checks instead of
    # blocking it for the full poke window (better for shared pools).
    # soft_fail=True: if no file ever shows up, this run is marked
    # "skipped" rather than "failed" - no false-alarm alerts on days
    # with no new data.
    detect_new_files = FileSensor(
        task_id="detect_new_excel_files",
        filepath="/opt/airflow/data/input/*.xlsx",
        fs_conn_id="fs_default",
        poke_interval=30,
        timeout=60 * 10,
        mode="reschedule",
        soft_fail=True,
    )

    # STAGE 2: Parse Excel files -> Parquet (Bronze Zone)
    ingest_data_task = PythonOperator(
        task_id="parse_excel_to_raw",
        python_callable=ingest_data.run,
    )

    # STAGE 3a: Bronze -> Silver Zone
    clean_data_task = PythonOperator(
        task_id="transform_to_clean_zone",
        python_callable=clean_data.run,
    )

    # STAGE 3b: Silver -> Gold Zone
    derive_data_task = PythonOperator(
        task_id="transform_to_gold_zone",
        python_callable=derive_data.run,
    )

    # STAGE 4: Send success notification (failure notifications are
    # handled separately by notify_on_failure above)
    notify_task = PythonOperator(
        task_id="send_completion_notification",
        python_callable=lambda: notify.run(status="SUCCESS"),
    )

    # Define the order of execution (the "edges" of the DAG)
    detect_new_files >> ingest_data_task >> clean_data_task >> derive_data_task >> notify_task'''

"""
AIRFLOW DAG that orchestrates the energy consumption pipeline:

    1. Check for Excel files     -> ShortCircuitOperator
    2. Parse Excel -> Parquet    -> ingest_data.py 
    3. Bronze -> Silver          -> clean_data.py
    4. Silver -> Gold            -> derive_data.py
    5. Completion notification   -> notify.py
"""

import logging
import os
import glob
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator, ShortCircuitOperator

sys.path.insert(0, "/opt/airflow/scripts")

import clean_data
import derive_data
import ingest_data
import notify

logger = logging.getLogger(__name__)

INPUT_DIR = "/opt/airflow/data/input"


def check_for_excel_files():
    """
    Check if any .xlsx files exist in the input directory.
    Returns True to continue the pipeline, False to stop it cleanly.
    """
    files = glob.glob(os.path.join(INPUT_DIR, "*.xlsx"))

    if not files:
        print(f"No Excel files found in {INPUT_DIR} - stopping pipeline.")
        return False

    print(f"Found {len(files)} Excel file(s):")
    for f in files:
        print(f"  - {os.path.basename(f)}")
    return True


def notify_on_failure(context):
    """
    Called automatically by Airflow if any task in the DAG fails.
    """
    task_instance = context.get("task_instance")
    exception = context.get("exception")
    try:
        notify.run(
            status="FAILURE",
            failed_task=task_instance.task_id if task_instance else "unknown",
            error=str(exception) if exception else "unknown error",
        )
    except Exception:
        logger.exception("notify_on_failure: failed to send failure notification")


default_args = {
    "owner": "data_engineering_team",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
    "on_failure_callback": notify_on_failure,
}


with DAG(
    dag_id="energy_pipeline_dag",
    description="Centralize, normalize, and aggregate energy consumption data from Excel files",
    default_args=default_args,
    start_date=datetime(2025, 1, 1),
    schedule_interval=timedelta(minutes=15),
    catchup=False,
    max_active_runs=1,
    tags=["energy", "medallion", "duckdb", "minio"],
) as dag:

    # STAGE 1: Check for Excel files (replaces FileSensor)
    check_files_task = ShortCircuitOperator(
        task_id="check_for_excel_files",
        python_callable=check_for_excel_files,
    )

    # STAGE 2: Parse Excel files -> Parquet (Bronze Zone)
    ingest_data_task = PythonOperator(
        task_id="parse_excel_to_bronze",
        python_callable=ingest_data.run,
    )

    # STAGE 3: Bronze -> Silver Zone
    clean_data_task = PythonOperator(
        task_id="transform_to_silver_zone",
        python_callable=clean_data.run,
    )

    # STAGE 4: Silver -> Gold Zone
    derive_data_task = PythonOperator(
        task_id="transform_to_gold_zone",
        python_callable=derive_data.run,
    )

    # STAGE 5: Success notification
    notify_task = PythonOperator(
        task_id="send_completion_notification",
        python_callable=lambda **context: notify.run(status="SUCCESS"),
    )

    # Pipeline order
    check_files_task >> ingest_data_task >> clean_data_task >> derive_data_task >> notify_task
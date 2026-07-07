"""
  Writes a timestamped log entry summarizing the pipeline run.

  Called in two places by the DAG:
    - On success: notify.run(status="SUCCESS")
    - On failure: notify.run(status="FAILURE", failed_task=..., error=...)
"""

import datetime
import os

LOG_FILE = "/opt/airflow/data/pipeline_log.txt"


def run(status="SUCCESS", failed_task=None, error=None, **context):
    """
    Main entry point, called by the Airflow PythonOperator (success path)
    or by the DAG's on_failure_callback (failure path).

    Writes a timestamped message reflecting the actual outcome, and
    returns it so it also shows up in the task's Airflow logs.
    """
    timestamp = datetime.datetime.utcnow().isoformat()

    if status == "FAILURE":
        message = (
            f"[{timestamp}] Pipeline run FAILED "
            f"(task: {failed_task or 'unknown'}) - {error or 'no error details'}\n"
        )
    else:
        message = f"[{timestamp}] Pipeline run completed successfully.\n"

    print(message.strip())

    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(message)

    return message


if __name__ == "__main__":
    run()
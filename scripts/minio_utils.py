"""
Helper utilities for interacting with MinIO (S3-compatible object storage).

This module centralizes connection logic so that other scripts do not need to
re-implement MinIO setup code.

MinIO is a self-hosted, S3-compatible object storage system used to store
and manage data in a structured data pipeline.

Buckets used:
    bronze:   Stores original Excel files and raw Parquet files.
    silver: Stores normalized and validated Parquet files.
    gold:  Stores aggregated, analytics-ready Parquet files.
"""

import os
import io
import socket

import boto3
from botocore.client import Config


def _load_env_file():
    """Load project .env values into os.environ without external dependencies."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_env_file()

def _resolve_minio_endpoint(endpoint: str | None = None) -> str:
    """
    Use the Docker hostname when running inside containers, but fall back to
    localhost when the script is executed directly from the host machine.
    """
    candidate = endpoint or os.getenv("MINIO_ENDPOINT_URL") or os.getenv("MINIO_ENDPOINT")

    if not candidate:
        return candidate

    if candidate.startswith("http://"):
        normalized = candidate
    elif candidate.startswith("https://"):
        normalized = candidate
    else:
        normalized = f"http://{candidate}"

    if "minio:9000" in normalized:
        try:
            socket.getaddrinfo("minio", 9000, proto=socket.IPPROTO_TCP)
        except OSError:
            normalized = normalized.replace("minio:9000", "localhost:9000")

    if candidate.startswith("minio:") and not normalized.startswith("http://"):
        normalized = f"http://{normalized}"

    return normalized


def get_s3_client():
    """
    Create and return a boto3 S3 client configured to talk to MinIO.

    The connection details come from environment variables 
    so this code never needs to be changed even if credentials change.
    """
    endpoint_url = _resolve_minio_endpoint(os.getenv("MINIO_ENDPOINT_URL"))
    access_key = os.getenv("MINIO_ROOT_USER")
    secret_key = os.getenv("MINIO_ROOT_PASSWORD")

    if not endpoint_url or not access_key or not secret_key:
        raise ValueError("Missing MinIO configuration. Check the project .env file.")

    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="eu-central-1", 
    )
    return client

def ensure_bucket_exists(client, bucket_name):
    existing_buckets = [b["Name"] for b in client.list_buckets().get("Buckets", [])]
    if bucket_name not in existing_buckets:
        client.create_bucket(Bucket=bucket_name)
        print(f"Created bucket: {bucket_name}")
    else:
        print(f"Bucket already exists: {bucket_name}")

def upload_bytes(client, bucket_name, object_key, data_bytes):
    """
    Upload raw bytes (e.g. an Excel file or Parquet file) to MinIO.
 
    Parameters:
        client       : boto3 S3 client (from get_s3_client())
        bucket_name  : e.g. 'bronze', 'silver', 'gold'
        object_key   : the "path" inside the bucket, e.g. 'hall1/H1.xlsx'
        data_bytes   : the file content as bytes
    """
    client.put_object(Bucket=bucket_name, Key=object_key, Body=data_bytes)
    print(f"Uploaded -> s3://{bucket_name}/{object_key}")

def upload_file(client, bucket_name, object_key, local_path):
    # Upload a local file (by path) to MinIO.
    with open(local_path, "rb") as f:
        upload_bytes(client, bucket_name, object_key, f.read())

def download_bytes(client, bucket_name, object_key):
    """
    Download an object from MinIO and return it as bytes.
    Useful for reading Parquet/Excel files into memory.
    """
    response = client.get_object(Bucket=bucket_name, Key=object_key)
    return response["Body"].read()
 
 
def list_objects(client, bucket_name, prefix=""):
    """
    List all object keys (file paths) inside a bucket, optionally
    filtered by a prefix (like a sub-folder).
 
    Returns a list of strings, e.g. ['hall1/H1.xlsx', 'hall2/H2.xlsx']
    """
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys
 
 
def get_duckdb_s3_setup_sql():
    """
    Returns the SQL statements needed to configure DuckDB so it can
    read/write Parquet files directly from/to MinIO using the
    'httpfs' extension.
 
    Every script that uses DuckDB with MinIO calls this function
    and runs the returned SQL first.
    """
    endpoint = os.getenv("MINIO_ENDPOINT")
    try:
        socket.getaddrinfo("minio", 9000, proto=socket.IPPROTO_TCP)
    except OSError:
        if endpoint and endpoint.startswith("minio:"):
            endpoint = endpoint.replace("minio:9000", "localhost:9000")

    access_key = os.getenv("MINIO_ROOT_USER")
    secret_key = os.getenv("MINIO_ROOT_PASSWORD")

    sql = f"""
        INSTALL httpfs;
        LOAD httpfs;
        SET s3_endpoint='{endpoint}';
        SET s3_access_key_id='{access_key}';
        SET s3_secret_access_key='{secret_key}';
        SET s3_use_ssl=false;
        SET s3_url_style='path';
    """
    return sql
 
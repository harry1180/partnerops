"""Object storage abstraction (S3-compatible: MinIO locally, S3 in prod).

Raw billing files and generated exports go through this boundary only; the
rest of the codebase never imports boto3 directly. put/get are synchronous
wrappers executed in threads from async code (boto3 is sync).
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import boto3
from botocore.config import Config as BotoConfig

from app.core.config import get_settings


@dataclass(frozen=True)
class StorageObject:
    key: str
    body: bytes
    sha256: str


class ObjectStorage:
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.object_storage_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.object_storage_endpoint,
            aws_access_key_id=settings.object_storage_access_key,
            aws_secret_access_key=settings.object_storage_secret_key,
            use_ssl=settings.object_storage_use_ssl,
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}),
            region_name="us-east-1",
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except Exception:
            self._client.create_bucket(Bucket=self.bucket)

    def put_bytes(self, key: str, body: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)
        return sha256_bytes(body)

    def get_bytes(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def stream_lines(self, key: str):  # type: ignore[no-untyped-def]
        body = self.get_bytes(key)
        yield from io.BytesIO(body)

    def presigned_download(self, key: str, expires_s: int = 900) -> str:
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_s
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def list_prefix(self, prefix: str, limit: int = 1000) -> list[str]:
        resp = self._client.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=limit)
        return [o["Key"] for o in resp.get("Contents", [])]


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        _storage = ObjectStorage()
    return _storage

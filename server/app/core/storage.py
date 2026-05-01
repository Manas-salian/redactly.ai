from __future__ import annotations

import hashlib
import shutil
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlencode
from uuid import uuid4

from app.config import Settings, get_settings


class BlobStore(ABC):
    @abstractmethod
    def put(self, key: str, data: BinaryIO) -> str:
        """Upload bytes; return the canonical URI (e.g. file:///... or s3://...)."""

    @abstractmethod
    def get(self, key: str) -> BinaryIO:
        """Open a binary stream for reading."""

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def signed_url(self, key: str, ttl_seconds: int = 300) -> str: ...

    @staticmethod
    def make_key(*parts: str) -> str:
        cleaned = [p.strip("/") for p in parts if p]
        cleaned.append(uuid4().hex)
        return "/".join(cleaned)

    @staticmethod
    def sha256(data: BinaryIO) -> str:
        h = hashlib.sha256()
        for chunk in iter(lambda: data.read(8192), b""):
            h.update(chunk)
        data.seek(0)
        return h.hexdigest()


class LocalBlobStore(BlobStore):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Disallow path traversal; keys are always relative.
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError(f"key {key!r} resolves outside storage root")
        return p

    def put(self, key: str, data: BinaryIO) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as fh:
            shutil.copyfileobj(data, fh)
        return p.as_uri()

    def get(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def signed_url(self, key: str, ttl_seconds: int = 300) -> str:
        # Local backend has no real signing; we return a pseudo-signed URL the
        # API layer recognizes and proxies. Format: blob+local://<key>?exp=...
        exp = int((datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).timestamp())
        return f"blob+local://{key}?{urlencode({'exp': exp})}"


def get_blob_store(settings: Settings | None = None) -> BlobStore:
    s = settings or get_settings()
    if s.storage_backend == "local":
        return LocalBlobStore(s.storage_local_root)
    raise NotImplementedError(
        f"storage_backend={s.storage_backend} is not implemented in V1.0; "
        "add S3/MinIO impls in a follow-up plan when needed"
    )

"""Extract step: read raw source files into DataFrames.

Extraction is intentionally light-touch (ELT): we read files as faithfully as
possible, normalise column names, and leave typing/cleaning to SQL staging
models.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from finflow.config import PipelineConfig, SourceConfig
from finflow.logging_utils import get_logger

logger = get_logger(__name__)


class ExtractError(RuntimeError):
    pass


@dataclass
class ExtractedSource:
    source: SourceConfig
    path: Path
    frame: pd.DataFrame
    checksum: str


def normalise_column(name: str) -> str:
    name = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip()).strip("_").lower()
    if not name:
        raise ExtractError("Empty column name after normalisation")
    if name[0].isdigit():
        name = f"c_{name}"
    return name


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path, source: SourceConfig) -> pd.DataFrame:
    return pd.read_csv(path, dtype=source.dtypes or None)


def _read_json(path: Path, source: SourceConfig) -> pd.DataFrame:
    with path.open() as fh:
        payload = json.load(fh)
    if isinstance(payload, dict):
        # Support {"data": [...]} envelopes common in API dumps.
        payload = payload.get("data", payload.get("records", [payload]))
    df = pd.json_normalize(payload)
    if source.dtypes:
        df = df.astype(source.dtypes)
    return df


def _read_parquet(path: Path, source: SourceConfig) -> pd.DataFrame:
    return pd.read_parquet(path)


READERS = {
    "csv": _read_csv,
    "json": _read_json,
    "parquet": _read_parquet,
}


def extract_source(config: PipelineConfig, source: SourceConfig) -> ExtractedSource:
    path = config.resolve_source_path(source)
    if not path.exists():
        raise ExtractError(f"Source file for {source.name!r} not found: {path}")
    reader = READERS.get(source.format.lower())
    if reader is None:
        raise ExtractError(f"Unsupported format {source.format!r} for {source.name!r}")

    df = reader(path, source)
    df.columns = [normalise_column(c) for c in df.columns]
    if df.columns.duplicated().any():
        dupes = sorted(set(df.columns[df.columns.duplicated()]))
        raise ExtractError(f"Duplicate columns after normalisation in {source.name}: {dupes}")

    for col in source.parse_dates:
        if col in df.columns:
            # Keep ISO strings in the raw layer; staging handles typing.
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")

    checksum = file_checksum(path)
    logger.info(
        "source_extracted",
        extra={"source": source.name, "path": str(path), "rows": len(df), "checksum": checksum},
    )
    return ExtractedSource(source=source, path=path, frame=df, checksum=checksum)


def extract_all(config: PipelineConfig) -> list[ExtractedSource]:
    return [extract_source(config, s) for s in config.sources]

"""Pipeline configuration loading.

Configuration is read from a YAML file and can be overridden with environment
variables prefixed with ``FINFLOW_`` (e.g. ``FINFLOW_WAREHOUSE_PATH``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/pipeline.yaml")


@dataclass
class SourceConfig:
    """A single raw source file to ingest."""

    name: str
    path: str
    format: str = "csv"
    table: str | None = None
    dtypes: dict[str, str] = field(default_factory=dict)
    parse_dates: list[str] = field(default_factory=list)

    @property
    def raw_table(self) -> str:
        return self.table or f"raw_{self.name}"


@dataclass
class FeatureConfig:
    return_windows: list[int] = field(default_factory=lambda: [1, 5, 21])
    volatility_windows: list[int] = field(default_factory=lambda: [21, 63])
    moving_average_windows: list[int] = field(default_factory=lambda: [20, 50])
    momentum_lookback: int = 126
    momentum_skip: int = 21


@dataclass
class PipelineConfig:
    warehouse_path: str = "data/warehouse.db"
    data_dir: str = "data/raw"
    checks_path: str = "config/checks.yaml"
    log_level: str = "INFO"
    log_file: str | None = None
    fail_on_error: bool = True
    sources: list[SourceConfig] = field(default_factory=list)
    features: FeatureConfig = field(default_factory=FeatureConfig)

    def resolve_source_path(self, source: SourceConfig) -> Path:
        path = Path(source.path)
        if path.is_absolute():
            return path
        return Path(self.data_dir) / path


_ENV_OVERRIDES = {
    "FINFLOW_WAREHOUSE_PATH": ("warehouse_path", str),
    "FINFLOW_DATA_DIR": ("data_dir", str),
    "FINFLOW_CHECKS_PATH": ("checks_path", str),
    "FINFLOW_LOG_LEVEL": ("log_level", str),
    "FINFLOW_LOG_FILE": ("log_file", str),
    "FINFLOW_FAIL_ON_ERROR": ("fail_on_error", lambda v: v.lower() in {"1", "true", "yes"}),
}


def _from_dict(raw: dict[str, Any]) -> PipelineConfig:
    sources = [SourceConfig(**s) for s in raw.get("sources", [])]
    features = FeatureConfig(**raw.get("features", {}))
    scalar_keys = {
        "warehouse_path",
        "data_dir",
        "checks_path",
        "log_level",
        "log_file",
        "fail_on_error",
    }
    scalars = {k: v for k, v in raw.items() if k in scalar_keys}
    return PipelineConfig(sources=sources, features=features, **scalars)


def load_config(path: str | Path | None = None) -> PipelineConfig:
    """Load pipeline config from YAML, then apply environment overrides."""
    config_path = Path(path or os.environ.get("FINFLOW_CONFIG", DEFAULT_CONFIG_PATH))
    raw: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open() as fh:
            raw = yaml.safe_load(fh) or {}
    config = _from_dict(raw)

    for env_key, (attr, cast) in _ENV_OVERRIDES.items():
        value = os.environ.get(env_key)
        if value is not None and value != "":
            setattr(config, attr, cast(value))
    return config

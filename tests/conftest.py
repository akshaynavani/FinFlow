from __future__ import annotations

from pathlib import Path

import pytest

from finflow.config import FeatureConfig, PipelineConfig, SourceConfig
from finflow.logging_utils import configure_logging
from finflow.pipeline import Pipeline
from finflow.sample_data import generate_dataset

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True, scope="session")
def _quiet_logs():
    configure_logging("WARNING")


def make_config(tmp_path: Path, **overrides) -> PipelineConfig:
    cfg = PipelineConfig(
        warehouse_path=str(tmp_path / "warehouse.db"),
        data_dir=str(tmp_path / "raw"),
        checks_path=str(ROOT / "config" / "checks.yaml"),
        log_level="WARNING",
        sources=[
            SourceConfig(name="prices", path="prices.csv", parse_dates=["date"]),
            SourceConfig(
                name="fundamentals",
                path="fundamentals.csv",
                parse_dates=["period_end", "report_date"],
            ),
            SourceConfig(name="securities", path="securities.json", format="json"),
            SourceConfig(
                name="corporate_actions", path="corporate_actions.csv", parse_dates=["ex_date"]
            ),
        ],
        features=FeatureConfig(),
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    generate_dataset(raw, days=300, seed=7)
    return raw


@pytest.fixture
def dirty_dataset(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    generate_dataset(raw, days=300, seed=7, with_defects=True)
    return raw


@pytest.fixture
def config(tmp_path: Path) -> PipelineConfig:
    return make_config(tmp_path)


@pytest.fixture
def pipeline(config: PipelineConfig):
    p = Pipeline(config)
    yield p
    p.close()

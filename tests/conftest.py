"""Shared pytest configuration: markers, dataset auto-skip and common fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "legacy" / "hackathon-2026-digital-continuity-dataset.csv"

MARKERS = {
    "property": "hypothesis property-based test",
    "integration": "test crossing module or process boundaries",
    "dataset": "needs data/legacy/hackathon-2026-digital-continuity-dataset.csv",
    "slow": "long-running test (optional 100,000-row run)",
}


def pytest_configure(config: pytest.Config) -> None:
    for name, doc in MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {doc}")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if DATASET.exists():
        return
    skip = pytest.mark.skip(reason=f"dataset CSV absent: {DATASET.relative_to(ROOT).as_posix()}")
    for item in items:
        if "dataset" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_real_llm_key(monkeypatch):
    """No test may reach the real LLM provider: the key is removed unless a test sets a fake one."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture(scope="session")
def cfg():
    from rules.config import load_rule_config
    return load_rule_config()


@pytest.fixture(scope="session")
def dataset_path() -> Path:
    return DATASET

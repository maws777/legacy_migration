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


class _NoOllama:
    """Default Ollama transport in tests: every request fails as if the server were stopped."""

    def handle_request(self, request):
        import httpx
        raise httpx.ConnectError("Ollama is disabled in the test suite", request=request)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """No test may reach a real LLM provider: no Anthropic key, and no real Ollama server.

    Tests that need a provider inject a fake client or an httpx mock transport.
    """
    import httpx

    from agents import llm_client

    class Blocked(_NoOllama, httpx.BaseTransport):
        pass

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm_client, "OLLAMA_TRANSPORT", Blocked())


@pytest.fixture(scope="session")
def cfg():
    from rules.config import load_rule_config
    return load_rule_config()


@pytest.fixture(scope="session")
def dataset_path() -> Path:
    return DATASET

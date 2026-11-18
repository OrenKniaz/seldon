import pytest


@pytest.fixture
def scalar_align(monkeypatch):
    """Replay the legacy scalar answers and separate-guide delivery."""
    monkeypatch.setenv("SELDON_OFF", "growAlign,sizeFirst")

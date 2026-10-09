"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _private_project_registry(tmp_path, monkeypatch):
    """Tests create projects: keep them out of this computer's list of WishBridge projects."""
    from wishbridge import discover

    monkeypatch.setattr(discover, "REGISTRY", tmp_path / "wishbridge-projects.json")

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMZ_STUDIO_HOME", str(tmp_path / "home"))

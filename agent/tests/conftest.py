import pytest


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Run each test away from the project, so a developer's .env is not loaded."""
    monkeypatch.chdir(tmp_path)

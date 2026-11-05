import pytest

from resources.tools._academic.common import Store


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    import core.user_context

    monkeypatch.setenv("SOTABAND_ACADEMIC_DATA", str(tmp_path / "academic"))
    monkeypatch.setenv("SOTABAND_USER_ID", "academic-test")
    monkeypatch.setattr(core.user_context, "_cached_user_id", None)


@pytest.fixture
def store():
    return Store()

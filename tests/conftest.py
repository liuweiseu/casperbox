import pytest


@pytest.fixture(autouse=True)
def no_user_plugins(tmp_path_factory, monkeypatch):
    """Keep the user's real plugins (~/.config/casperbox/versions, CASPERBOX_PLUGIN_PATH) out of the tests."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    monkeypatch.delenv("CASPERBOX_PLUGIN_PATH", raising=False)

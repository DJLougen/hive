from confload import DEFAULTS, load_config


def test_env_overrides_file():
    cfg = load_config({"port": "1111"}, environ={"APP_PORT": "9090"})
    assert cfg["port"] == "9090"


def test_file_overrides_defaults():
    cfg = load_config({"port": "1111"}, environ={})
    assert cfg["port"] == "1111"


def test_defaults_fill_gaps():
    cfg = load_config({}, environ={})
    assert cfg == DEFAULTS


def test_env_keys_lowercased():
    cfg = load_config({}, environ={"APP_HOST": "0.0.0.0"})
    assert cfg["host"] == "0.0.0.0"


def test_unrelated_env_ignored():
    cfg = load_config({}, environ={"PATH": "/usr/bin", "APP_DEBUG": "true"})
    assert "path" not in cfg
    assert cfg["debug"] == "true"

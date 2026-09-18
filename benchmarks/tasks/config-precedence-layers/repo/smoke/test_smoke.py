"""Smoke tests: the public API happy path. Not the grading criterion."""

from __future__ import annotations

from settings import Schema, Setting, load_settings, ordered_layers

SCHEMA = Schema([Setting("host", "str", "localhost"), Setting("port", "int", 80)])


def test_defaults_are_used_when_nothing_overrides_them():
    s = load_settings(SCHEMA)
    assert s.get("host") == "localhost" and s.source_of("host") == "default"


def test_a_file_value_wins_over_the_default():
    s = load_settings(SCHEMA, file="host = db.internal\n")
    assert s.get("host") == "db.internal" and s.source_of("host") == "file"


def test_the_environment_comes_after_the_file():
    s = load_settings(SCHEMA, file="port = 5432\n", environ={"APP_PORT": "6432"})
    assert s.get("port") == 6432 and s.source_of("port") == "env"


def test_an_explicit_override_is_applied():
    s = load_settings(SCHEMA, explicit={"port": 9999})
    assert s.get("port") == 9999 and s.source_of("port") == "explicit"


def test_unknown_keys_are_ignored():
    s = load_settings(SCHEMA, file="nosuchkey = 1\n")
    assert s.as_dict() == {"host": "localhost", "port": 80}


def test_ordered_layers_is_lowest_first():
    from settings import Defaults, EnvLayer, ExplicitLayer, FileLayer

    layers = [ExplicitLayer(), EnvLayer({}), FileLayer(""), Defaults({})]
    assert [layer.name for layer in ordered_layers(layers)] == [
        "default", "file", "env", "explicit"]


# --- Spec coverage: the issue's stated requirements, not just the happy path.
# These fail on the shipped repo; the held-out oracle checks the edge cases.


def test_explicit_beats_the_environment():
    s = load_settings(SCHEMA, environ={"APP_PORT": "6432"}, explicit={"port": 9999})
    assert s.get("port") == 9999 and s.source_of("port") == "explicit"


def test_all_four_layers_pick_the_highest_defined_value():
    s = load_settings(
        SCHEMA,
        file="host = from-file\nport = 1111\n",
        environ={"APP_PORT": "2222"},
        explicit={"port": 3333},
    )
    assert s.get("port") == 3333 and s.get("host") == "from-file"
    assert s.source_of("port") == "explicit" and s.source_of("host") == "file"


def test_invalid_values_are_reported_all_at_once():
    import pytest

    schema = Schema([Setting("port", "int", 80), Setting("debug", "bool", False)])
    with pytest.raises(ValueError) as err:
        load_settings(schema, explicit={"port": "not-a-number", "debug": "maybe"})
    message = str(err.value)
    assert "port" in message and "debug" in message

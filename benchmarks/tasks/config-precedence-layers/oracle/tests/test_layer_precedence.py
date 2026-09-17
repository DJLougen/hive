"""Held-out grading tests for the settings precedence chain."""

from __future__ import annotations

import pytest

from settings import (LAYER_ORDER, Defaults, EnvLayer, ExplicitLayer, FileLayer, Schema,
                      Setting, load_settings, ordered_layers)

SCHEMA = Schema([
    Setting("host", "str", "localhost"),
    Setting("port", "int", 80),
    Setting("debug", "bool", False),
])


def test_the_documented_order_is_lowest_first():
    assert LAYER_ORDER == ("default", "file", "env", "explicit")


def test_every_pair_of_layers_resolves_in_that_order():
    layers = [Defaults({}), FileLayer(""), EnvLayer({}), ExplicitLayer({})]
    assert [layer.name for layer in ordered_layers(layers)] == list(LAYER_ORDER)


def test_explicit_beats_the_environment():
    s = load_settings(SCHEMA, environ={"APP_PORT": "6432"}, explicit={"port": 9999})
    assert s.get("port") == 9999
    assert s.source_of("port") == "explicit"


def test_environment_beats_the_file():
    s = load_settings(SCHEMA, file="host = from-file\n", environ={"APP_HOST": "from-env"})
    assert (s.get("host"), s.source_of("host")) == ("from-env", "env")


def test_file_beats_the_default_and_the_default_survives_a_gap():
    s = load_settings(SCHEMA, file="debug = true\n")
    assert (s.get("debug"), s.source_of("debug")) == (True, "file")
    assert (s.get("host"), s.source_of("host")) == ("localhost", "default")


def test_all_four_layers_together_pick_the_highest_defined_value():
    s = load_settings(
        SCHEMA,
        file="host = from-file\nport = 1111\n",
        environ={"APP_PORT": "2222", "APP_DEBUG": "true"},
        explicit={"port": 3333},
    )
    assert s.as_dict() == {"host": "from-file", "port": 3333, "debug": True}
    assert s.source_of("port") == "explicit"
    assert s.source_of("debug") == "env"
    assert s.source_of("host") == "file"


def test_values_are_coerced_using_the_schema_type_of_the_winning_layer():
    s = load_settings(SCHEMA, environ={"APP_PORT": " 8080 ", "APP_DEBUG": "yes"})
    assert s.get("port") == 8080 and isinstance(s.get("port"), int)
    assert s.get("debug") is True
    with pytest.raises(ValueError):
        load_settings(SCHEMA, explicit={"port": "not-a-number"})


def test_an_unknown_key_never_reaches_the_result():
    s = load_settings(SCHEMA, file="unknown = 1\n", explicit={"also_unknown": 2})
    assert s.as_dict() == {"host": "localhost", "port": 80, "debug": False}
    assert s.sources().get("unknown") is None


def test_sources_reports_every_known_setting_and_only_known_ones():
    s = load_settings(SCHEMA, file="host = from-file\n",
                      environ={"APP_PORT": "2222"}, explicit={"debug": True})
    assert s.sources() == {"host": "file", "port": "env", "debug": "explicit"}
    assert s.sources().keys() == {"host", "port", "debug"}


def test_values_and_sources_stay_consistent():
    s = load_settings(SCHEMA, environ={"APP_HOST": "from-env"})
    assert s.sources()["host"] == "env"
    assert s.get("host") == "from-env"


def test_invalid_values_are_reported_all_at_once():
    # Two bad values: the error must name both, not stop at the first.
    with pytest.raises(ValueError) as err:
        load_settings(SCHEMA, explicit={"port": "not-a-number", "debug": "maybe"})
    message = str(err.value)
    assert "port" in message and "debug" in message
    assert "host" not in message


def test_one_invalid_value_is_still_reported():
    with pytest.raises(ValueError) as err:
        load_settings(SCHEMA, file="port = abc\n")
    assert "port" in str(err.value)

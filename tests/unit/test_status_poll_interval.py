"""Tests for the configurable machine status poll interval."""

from __future__ import annotations

import pytest

from carveracontroller import Controller as controller_module

Controller = controller_module.Controller


@pytest.fixture
def fake_config(monkeypatch):
    """Stand in for kivy's Config, which isn't running in unit tests."""
    import types

    values = {}
    fake = types.SimpleNamespace(get=lambda section, key: values[key])
    module = types.ModuleType("kivy.config")
    module.Config = fake
    monkeypatch.setitem(__import__("sys").modules, "kivy.config", module)
    # refresh_status_poll_interval short-circuits to the default when App is None.
    monkeypatch.setattr(controller_module, "App", object())
    return values


def refresh(values, raw) -> float:
    ctrl = Controller.__new__(Controller)
    values["status_poll_interval_ms"] = raw
    return ctrl.refresh_status_poll_interval()


def test_default_is_unchanged_at_200ms(fake_config):
    assert refresh(fake_config, "200") == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("50", 0.05), ("100", 0.1), ("150", 0.15), ("300", 0.3), ("500", 0.5), ("1000", 1.0)],
)
def test_every_offered_option_maps_to_the_expected_interval(fake_config, raw, expected):
    assert refresh(fake_config, raw) == pytest.approx(expected)


def test_faster_than_the_floor_is_clamped(fake_config):
    """The 20 Hz floor protects the 230400-baud RS-422 link to the motion controller."""
    assert refresh(fake_config, "1") == pytest.approx(controller_module.STREAM_POLL_MIN)
    assert refresh(fake_config, "0") == pytest.approx(controller_module.STREAM_POLL_MIN)
    assert refresh(fake_config, "-100") == pytest.approx(controller_module.STREAM_POLL_MIN)


def test_slower_than_the_ceiling_is_clamped(fake_config):
    assert refresh(fake_config, "999999") == pytest.approx(controller_module.STREAM_POLL_MAX)


@pytest.mark.parametrize("raw", ["", "abc", None])
def test_unparseable_values_fall_back_to_the_default(fake_config, raw):
    assert refresh(fake_config, raw) == pytest.approx(controller_module.STREAM_POLL)


def test_missing_setting_falls_back_to_the_default(monkeypatch):
    import sys
    import types

    def exploding_get(section, key):
        raise KeyError(key)

    module = types.ModuleType("kivy.config")
    module.Config = types.SimpleNamespace(get=exploding_get)
    monkeypatch.setitem(sys.modules, "kivy.config", module)
    monkeypatch.setattr(controller_module, "App", object())

    ctrl = Controller.__new__(Controller)
    assert ctrl.refresh_status_poll_interval() == pytest.approx(controller_module.STREAM_POLL)


def test_headless_without_kivy_app_uses_the_default(monkeypatch):
    monkeypatch.setattr(controller_module, "App", None)

    ctrl = Controller.__new__(Controller)
    assert ctrl.refresh_status_poll_interval() == pytest.approx(controller_module.STREAM_POLL)


def test_class_default_matches_stream_poll():
    """streamIO reads self.stream_poll, so the unconfigured default must be sane."""
    assert Controller.stream_poll == pytest.approx(controller_module.STREAM_POLL)


def test_floor_leaves_headroom_on_the_rs422_link():
    """
    Sanity-check the documented safety argument rather than just the number: at the fastest
    allowed rate a ~150 byte status response must stay a small fraction of the 230400 8N1
    mainboard link (~23040 bytes/s).
    """
    bytes_per_second = 230400 / 10
    worst_case = 150 / controller_module.STREAM_POLL_MIN
    assert worst_case / bytes_per_second < 0.20

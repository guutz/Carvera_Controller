"""Tests for MacroPadPendant (the CNC-specific driver on top of macropad.Daemon)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from carveracontroller.addons.pendant import pendant as pendant_module
from carveracontroller.Controller import Controller

MacroPadPendant = pendant_module.MacroPadPendant


class FakeDaemon:
    def __init__(self, num_rows=6, num_cols=21):
        self.pressed_keys: set[int] = set()
        self.num_rows = num_rows
        self.num_cols = num_cols
        self.led_calls: list[tuple[int, int]] = []
        self.text_calls: list[tuple[int, str]] = []
        self.brightness_calls: list[int] = []

    def set_led(self, key: int, color: int) -> None:
        self.led_calls.append((key, color))

    def set_text(self, row: int, text: str) -> None:
        self.text_calls.append((row, text))

    def set_brightness(self, percent: int) -> None:
        self.brightness_calls.append(percent)


class FakeController:
    def __init__(self, jog_mode=Controller.JOG_MODE_STEP):
        self.jog_mode = jog_mode
        self.continuous_jog_active = False
        self.calls: list[tuple] = []

    def jog(self, move):
        self.calls.append(("jog", move))

    def startContinuousJog(self, move, feed):
        self.calls.append(("start", move, feed))
        self.continuous_jog_active = True

    def stopContinuousJog(self):
        self.calls.append(("stop",))
        self.continuous_jog_active = False

    def abortCommand(self):
        self.calls.append(("abort",))

    def estopCommand(self):
        self.calls.append(("estop",))

    def gotoMachineHome(self):
        self.calls.append(("m_home",))

    def gotoSafeZ(self):
        self.calls.append(("safe_z",))

    def gotoWCSHome(self):
        self.calls.append(("w_home",))

    def setSpindleSwitch(self, on):
        self.calls.append(("spindle", on))


def make_pendant(cnc_vars=None, jog_mode=Controller.JOG_MODE_STEP, jogging_enabled=True) -> MacroPadPendant:
    pendant = MacroPadPendant.__new__(MacroPadPendant)
    pendant._cnc = SimpleNamespace(vars=cnc_vars if cnc_vars is not None else {})
    pendant._controller = FakeController(jog_mode=jog_mode)
    pendant._daemon = FakeDaemon()
    pendant._step_index = 1  # 0.1mm
    pendant._last_jog_direction = {}
    pendant._active_continuous_jog_axis = None
    pendant._led_cache = {}
    pendant._text_cache = {}
    pendant._max_jog_speed = MacroPadPendant.DEFAULT_MAX_JOG_SPEED
    pendant._brightness = 30
    pendant._is_jogging_enabled = lambda: jogging_enabled
    pendant._handle_run_pause_resume = lambda: pendant._controller.calls.append(("run_pause",))
    pendant._handle_probe_z = lambda: pendant._controller.calls.append(("probe_z",))
    pendant._button_presses: list[str] = []
    pendant._update_ui_on_button_press = lambda action: pendant._button_presses.append(action)
    pendant._jog_stops: list[bool] = []
    pendant._update_ui_on_jog_stop = lambda: pendant._jog_stops.append(True)
    return pendant


# --- _safe_number ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "lo", "hi", "expected"),
    [
        (0, 0, 100, 0.0),
        (50, 0, 100, 50.0),
        (150, 0, 100, 100.0),
        (-5, 0, 100, 0.0),
        (-5, -100, 100, -5.0),  # negative allowed when lo permits it (e.g. work positions)
        ("42.5", 0, 100, 42.5),
        ("invalid", 0, 100, 0.0),
        (None, 0, 100, 0.0),
        # Non-finite values are always treated as invalid -> 0, regardless of sign or lo/hi.
        (float("nan"), 0, 100, 0.0),
        (float("inf"), 0, 100, 0.0),
        (float("-inf"), 0, 100, 0.0),
        pytest.param(10**10000, 0, 100, 100.0, id="huge-positive-int"),
        pytest.param(-(10**10000), 0, 100, 0.0, id="huge-negative-int"),
        pytest.param(-(10**10000), -100, 100, -100.0, id="huge-negative-int-with-negative-lo"),
    ],
)
def test_safe_number(value, lo, hi, expected):
    assert MacroPadPendant._safe_number(value, lo, hi) == expected


# --- display text ------------------------------------------------------------------------


def test_refresh_display_text_formats_expected_rows():
    pendant = make_pendant(
        cnc_vars={"wx": 1.0, "wy": -2.5, "wz": 0.125, "wa": 0.0, "curfeed": 1200, "curspindle": 10000, "state": "Idle"}
    )
    pendant._refresh_display_text(pendant._daemon)

    rows = dict(pendant._daemon.text_calls)
    assert rows[0] == "X 1.000"
    assert rows[1] == "Y -2.500"
    assert rows[2] == "Z 0.125"
    assert rows[3] == "A 0.000"
    assert rows[4] == "F1200 S10000"
    assert rows[5] == "Idle step=0.1mm"


def test_refresh_display_text_noop_before_id_handshake():
    pendant = make_pendant()
    pendant._daemon.num_rows = 0

    pendant._refresh_display_text(pendant._daemon)

    assert pendant._daemon.text_calls == []


def test_refresh_display_text_dedupes_unchanged_rows():
    pendant = make_pendant(
        cnc_vars={"wx": 1.0, "wy": 0, "wz": 0, "wa": 0, "curfeed": 0, "curspindle": 0, "state": "Idle"}
    )

    pendant._refresh_display_text(pendant._daemon)
    first_call_count = len(pendant._daemon.text_calls)
    assert first_call_count == 6

    pendant._refresh_display_text(pendant._daemon)  # nothing changed
    assert len(pendant._daemon.text_calls) == first_call_count

    pendant._cnc.vars["wx"] = 2.0
    pendant._refresh_display_text(pendant._daemon)
    assert len(pendant._daemon.text_calls) == first_call_count + 1
    assert pendant._daemon.text_calls[-1] == (0, "X 2.000")


def test_refresh_display_text_truncates_to_cols():
    pendant = make_pendant(cnc_vars={"state": "ThisStateNameIsWayTooLongToFitOnOneRow"})
    pendant._daemon.num_cols = 8

    pendant._refresh_display_text(pendant._daemon)

    row5 = dict(pendant._daemon.text_calls)[5]
    assert len(row5) == 8


# --- LEDs ----------------------------------------------------------------------------------


def test_refresh_leds_alarm_overrides_everything():
    pendant = make_pendant(cnc_vars={"state": "Alarm"})

    pendant._refresh_leds(pendant._daemon)

    assert len(pendant._daemon.led_calls) == 12
    assert all(color == 0xFF0000 for _key, color in pendant._daemon.led_calls)


def test_refresh_leds_highlights_held_jog_axis():
    pendant = make_pendant(cnc_vars={"state": "Idle"})
    pendant._daemon.pressed_keys = {MacroPadPendant.KEY_JOG_Y}

    pendant._refresh_leds(pendant._daemon)

    colors = dict(pendant._daemon.led_calls)
    assert colors[MacroPadPendant.KEY_JOG_Y] == MacroPadPendant.AXIS_COLOR_HELD["Y"]
    assert colors[MacroPadPendant.KEY_JOG_X] == MacroPadPendant.AXIS_COLOR_IDLE["X"]
    assert colors[MacroPadPendant.KEY_JOG_Z] == MacroPadPendant.AXIS_COLOR_IDLE["Z"]


def test_refresh_leds_dedupes_unchanged():
    pendant = make_pendant(cnc_vars={"state": "Idle"})

    pendant._refresh_leds(pendant._daemon)
    first_count = len(pendant._daemon.led_calls)
    assert first_count == 12

    pendant._refresh_leds(pendant._daemon)
    assert len(pendant._daemon.led_calls) == first_count  # nothing changed, no new calls


# --- key press dispatch ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected_call", "expected_action"),
    [
        (MacroPadPendant.KEY_START_PAUSE, ("run_pause",), "start_pause"),
        (MacroPadPendant.KEY_STOP, ("abort",), "stop"),
        (MacroPadPendant.KEY_RESET, ("estop",), "reset"),
        (MacroPadPendant.KEY_PROBE_Z, ("probe_z",), "probe_z"),
        (MacroPadPendant.KEY_M_HOME, ("m_home",), "m_home"),
        (MacroPadPendant.KEY_SAFE_Z, ("safe_z",), "safe_z"),
        (MacroPadPendant.KEY_W_HOME, ("w_home",), "w_home"),
    ],
)
def test_handle_key_press_dispatches_fixed_actions(key, expected_call, expected_action):
    pendant = make_pendant()

    pendant._handle_key_press(pendant._daemon, key)

    assert expected_call in pendant._controller.calls
    assert pendant._button_presses == [expected_action]


def test_handle_key_press_jog_keys_are_noop():
    pendant = make_pendant()

    for key in MacroPadPendant.JOG_KEY_AXIS:
        pendant._handle_key_press(pendant._daemon, key)

    assert pendant._controller.calls == []
    assert pendant._button_presses == []


def test_handle_key_press_macro_key_runs_macro_1(monkeypatch):
    pendant = make_pendant()
    ran = []
    pendant.run_macro = lambda macro_id: ran.append(macro_id)

    pendant._handle_key_press(pendant._daemon, MacroPadPendant.KEY_MACRO_1)

    assert ran == [1]


def test_do_spindle_toggle_turns_on_when_off():
    pendant = make_pendant(cnc_vars={"curspindle": 0})

    pendant._handle_key_press(pendant._daemon, MacroPadPendant.KEY_SPINDLE_ON_OFF)

    assert ("spindle", True) in pendant._controller.calls


def test_do_spindle_toggle_respects_lasermode():
    pendant = make_pendant(cnc_vars={"curspindle": 0, "lasermode": True})

    pendant._handle_key_press(pendant._daemon, MacroPadPendant.KEY_SPINDLE_ON_OFF)

    assert pendant._controller.calls == []


# --- encoder / jogging -----------------------------------------------------------------------


def test_handle_encoder_delta_ignored_when_jogging_disabled():
    pendant = make_pendant(jogging_enabled=False)
    pendant._daemon.pressed_keys = {MacroPadPendant.KEY_JOG_X}

    pendant._handle_encoder_delta(pendant._daemon, 3)

    assert pendant._controller.calls == []


def test_handle_encoder_delta_ignored_when_no_axis_held():
    pendant = make_pendant()

    pendant._handle_encoder_delta(pendant._daemon, 3)

    assert pendant._controller.calls == []


def test_handle_encoder_delta_step_mode_jogs_by_step_size():
    pendant = make_pendant(jog_mode=Controller.JOG_MODE_STEP)
    pendant._daemon.pressed_keys = {MacroPadPendant.KEY_JOG_Y}
    pendant._step_index = 1  # 0.1mm

    pendant._handle_encoder_delta(pendant._daemon, 3)

    assert pendant._controller.calls == [("jog", "Y0.3")]


def test_handle_encoder_delta_continuous_mode_starts_jog_and_caps_z():
    pendant = make_pendant(jog_mode=Controller.JOG_MODE_CONTINUOUS)
    pendant._daemon.pressed_keys = {MacroPadPendant.KEY_JOG_Z}
    pendant._step_index = 3  # 10.0mm -> 100% of max speed, but Z is capped

    pendant._handle_encoder_delta(pendant._daemon, 1)

    assert pendant._controller.calls == [("start", "Z1", MacroPadPendant.Z_MAX_JOG_SPEED)]
    assert pendant._active_continuous_jog_axis == "Z"


def test_handle_encoder_delta_continuous_mode_direction_change_stops_jog():
    pendant = make_pendant(jog_mode=Controller.JOG_MODE_CONTINUOUS)
    pendant._daemon.pressed_keys = {MacroPadPendant.KEY_JOG_X}

    pendant._handle_encoder_delta(pendant._daemon, 1)  # starts jog in + direction
    assert pendant._controller.continuous_jog_active is True

    pendant._handle_encoder_delta(pendant._daemon, -1)  # reverse direction -> should stop

    assert ("stop",) in pendant._controller.calls


def test_handle_key_release_stops_active_continuous_jog():
    pendant = make_pendant(jog_mode=Controller.JOG_MODE_CONTINUOUS)
    pendant._daemon.pressed_keys = {MacroPadPendant.KEY_JOG_X}
    pendant._handle_encoder_delta(pendant._daemon, 1)
    assert pendant._controller.continuous_jog_active is True

    pendant._handle_key_release(pendant._daemon, MacroPadPendant.KEY_JOG_X)

    assert pendant._controller.continuous_jog_active is False
    assert pendant._active_continuous_jog_axis is None
    assert pendant._jog_stops == [True]


def test_handle_key_release_of_unrelated_key_is_noop():
    pendant = make_pendant()

    pendant._handle_key_release(pendant._daemon, MacroPadPendant.KEY_STOP)  # must not raise

    assert pendant._controller.calls == []


def test_handle_encoder_press_cycles_and_wraps_step_index():
    pendant = make_pendant()
    pendant._step_index = 0

    seen = []
    for _ in range(len(MacroPadPendant.STEP_SIZES) + 1):
        pendant._handle_encoder_press(pendant._daemon)
        seen.append(pendant._step_index)

    assert seen == [1, 2, 3, 0, 1]
    assert pendant._button_presses == ["step_size_changed"] * (len(MacroPadPendant.STEP_SIZES) + 1)


# --- connect lifecycle -----------------------------------------------------------------------


def test_handle_connect_resets_caches_sends_brightness_and_reports():
    pendant = make_pendant()
    pendant._led_cache = {0: 0x123456}
    pendant._text_cache = {0: "stale"}
    reported = []
    pendant._report_connection = lambda: reported.append(True)

    pendant._handle_connect(pendant._daemon)

    assert pendant._led_cache == {}
    assert pendant._text_cache == {}
    assert pendant._daemon.brightness_calls == [30]
    assert reported == [True]

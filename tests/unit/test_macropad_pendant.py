"""Tests for MacroPadPendant (the CNC-specific driver on top of macropad.Daemon)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from carveracontroller.addons.pendant import pendant as pendant_module
from carveracontroller.Controller import Controller

MacroPadPendant = pendant_module.MacroPadPendant

GOTO = MacroPadPendant.KEY_GOTO
ACT = MacroPadPendant.KEY_ACT
SET = MacroPadPendant.KEY_SET


class FakeDaemon:
    def __init__(self, num_rows=6, num_cols=21, firmware_version=2):
        self.pressed_keys: set[int] = set()
        self.num_rows = num_rows
        self.num_cols = num_cols
        self.firmware_version = firmware_version
        self.led_calls: list[tuple[int, int]] = []
        self.text_calls: list[tuple[int, str]] = []
        self.banner_calls: list[str] = []
        self.hint_calls: list[str] = []
        self.brightness_calls: list[int] = []

    @property
    def supports_banner(self) -> bool:
        return self.firmware_version >= 2

    def set_led(self, key: int, color: int) -> None:
        self.led_calls.append((key, color))

    def set_text(self, row: int, text: str) -> None:
        self.text_calls.append((row, text))

    def set_banner(self, text: str) -> None:
        self.banner_calls.append(text)

    def set_hint(self, text: str) -> None:
        self.hint_calls.append(text)

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

    def gotoMachineHome(self):
        self.calls.append(("m_home",))

    def gotoSafeZ(self):
        self.calls.append(("safe_z",))

    def gotoWCSHome(self):
        self.calls.append(("w_home",))

    def setSpindleSwitch(self, on):
        self.calls.append(("spindle", on))

    def wcs_set(self, x=None, y=None, z=None, a=None):
        self.calls.append(("wcs_set", x, y, z, a))


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
    pendant._banner_cache = ("", "")
    pendant._modifier_stack = []
    pendant._pending_confirm = None
    pendant._pending_deadline = 0.0
    pendant._flash_label = ""
    pendant._flash_until = 0.0
    pendant._max_jog_speed = MacroPadPendant.DEFAULT_MAX_JOG_SPEED
    pendant._brightness = 30
    pendant._is_jogging_enabled = lambda: jogging_enabled
    pendant._handle_run_pause_resume = lambda: pendant._controller.calls.append(("run_pause",))
    pendant._handle_probe_z = lambda: pendant._controller.calls.append(("probe_z",))
    pendant._button_presses: list[str] = []
    pendant._update_ui_on_button_press = lambda action: pendant._button_presses.append(action)
    pendant._jog_stops: list[bool] = []
    pendant._update_ui_on_jog_stop = lambda: pendant._jog_stops.append(True)
    pendant.run_macro = lambda macro_id: pendant._controller.calls.append(("macro", macro_id))

    # Use the real binding table rather than a copy, so these tests can't silently drift
    # out of sync with the layout the driver actually ships.
    pendant._targets = pendant._build_targets()
    return pendant


def hold(pendant: MacroPadPendant, key: int) -> None:
    pendant._daemon.pressed_keys.add(key)
    pendant._handle_key_press(pendant._daemon, key)


def release(pendant: MacroPadPendant, key: int) -> None:
    pendant._daemon.pressed_keys.discard(key)
    pendant._handle_key_release(pendant._daemon, key)


def tap(pendant: MacroPadPendant, key: int) -> None:
    hold(pendant, key)
    release(pendant, key)


# --- the "two controls at once" rule -------------------------------------------------------


@pytest.mark.parametrize("key", list(range(12)))
def test_no_lone_keypress_ever_acts(key):
    """The core safety property: a single key press must never command the machine."""
    pendant = make_pendant()

    tap(pendant, key)

    assert pendant._controller.calls == []


def test_target_without_modifier_is_inert_even_after_modifier_released():
    pendant = make_pendant()

    tap(pendant, GOTO)  # press and release the modifier
    tap(pendant, 7)  # then press what would have been "safe Z"

    assert pendant._controller.calls == []


# --- modifier + target chords ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("modifier", "target", "expected_call", "expected_action"),
    [
        (GOTO, 6, ("m_home",), "m_home"),
        (GOTO, 7, ("safe_z",), "safe_z"),
        (GOTO, 8, ("w_home",), "w_home"),
        (ACT, 3, ("run_pause",), "start_pause"),
        (ACT, 4, ("abort",), "stop"),
        (ACT, 6, ("probe_z",), "probe_z"),
    ],
)
def test_modifier_plus_target_fires_action(modifier, target, expected_call, expected_action):
    pendant = make_pendant(cnc_vars={"curspindle": 0})

    hold(pendant, modifier)
    tap(pendant, target)

    assert expected_call in pendant._controller.calls
    assert expected_action in pendant._button_presses


def test_act_plus_macro_key_runs_macro_1():
    pendant = make_pendant()

    hold(pendant, ACT)
    tap(pendant, 7)

    assert ("macro", 1) in pendant._controller.calls


def test_act_plus_spindle_toggles_on_when_off():
    pendant = make_pendant(cnc_vars={"curspindle": 0})

    hold(pendant, ACT)
    tap(pendant, 5)

    assert ("spindle", True) in pendant._controller.calls


def test_act_plus_spindle_respects_lasermode():
    pendant = make_pendant(cnc_vars={"curspindle": 0, "lasermode": True})

    hold(pendant, ACT)
    tap(pendant, 5)

    assert pendant._controller.calls == []


def test_target_key_unbound_under_this_modifier_does_nothing():
    pendant = make_pendant()

    hold(pendant, GOTO)
    tap(pendant, 3)  # 3 is an ACT target, not a GOTO target

    assert pendant._controller.calls == []


def test_same_key_means_different_things_under_different_modifiers():
    pendant = make_pendant()

    hold(pendant, GOTO)
    tap(pendant, 6)  # -> machine home
    release(pendant, GOTO)

    hold(pendant, ACT)
    tap(pendant, 6)  # -> probe Z
    release(pendant, ACT)

    assert ("m_home",) in pendant._controller.calls
    assert ("probe_z",) in pendant._controller.calls


def test_most_recently_pressed_modifier_wins():
    pendant = make_pendant()

    hold(pendant, GOTO)
    hold(pendant, ACT)  # roll onto ACT without releasing GOTO
    tap(pendant, 6)  # 6 is m-home under GOTO, probe Z under ACT

    assert ("probe_z",) in pendant._controller.calls
    assert ("m_home",) not in pendant._controller.calls


def test_releasing_newest_modifier_falls_back_to_still_held_one():
    pendant = make_pendant()

    hold(pendant, GOTO)
    hold(pendant, ACT)
    release(pendant, ACT)
    tap(pendant, 6)  # GOTO is still held -> m-home

    assert ("m_home",) in pendant._controller.calls


# --- confirmation flow ----------------------------------------------------------------------


def test_zero_xy_requires_two_presses():
    pendant = make_pendant()

    hold(pendant, SET)
    tap(pendant, 0)
    assert pendant._controller.calls == []  # first press only arms it
    assert pendant._pending_confirm == (SET, 0)

    tap(pendant, 0)
    assert ("wcs_set", 0, 0, None, None) in pendant._controller.calls
    assert pendant._pending_confirm is None


def test_zero_z_requires_two_presses():
    pendant = make_pendant()

    hold(pendant, SET)
    tap(pendant, 2)
    assert pendant._controller.calls == []

    tap(pendant, 2)
    assert ("wcs_set", None, None, 0, None) in pendant._controller.calls


def test_zero_xy_bound_to_both_x_and_y_keys():
    pendant = make_pendant()

    hold(pendant, SET)
    tap(pendant, 1)
    tap(pendant, 1)

    assert ("wcs_set", 0, 0, None, None) in pendant._controller.calls


def test_confirming_a_different_target_rearms_instead_of_firing():
    pendant = make_pendant()

    hold(pendant, SET)
    tap(pendant, 0)  # arm ZERO XY
    tap(pendant, 2)  # switch to ZERO Z -- must not fire either

    assert pendant._controller.calls == []
    assert pendant._pending_confirm == (SET, 2)


def test_releasing_modifier_cancels_pending_confirm():
    pendant = make_pendant()

    hold(pendant, SET)
    tap(pendant, 0)
    assert pendant._pending_confirm is not None

    release(pendant, SET)
    assert pendant._pending_confirm is None

    hold(pendant, SET)
    tap(pendant, 0)  # must be a fresh arm, not a confirm
    assert pendant._controller.calls == []


def test_encoder_press_cancels_pending_confirm_without_changing_step():
    pendant = make_pendant()
    step_before = pendant.current_step_size

    hold(pendant, SET)
    tap(pendant, 0)
    pendant._handle_encoder_press(pendant._daemon)

    assert pendant._pending_confirm is None
    assert pendant.current_step_size == step_before
    assert pendant._controller.calls == []


def test_pending_confirm_expires_after_timeout(monkeypatch):
    pendant = make_pendant()
    fake_now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: fake_now[0])

    hold(pendant, SET)
    tap(pendant, 0)
    assert pendant._pending_confirm is not None

    fake_now[0] += MacroPadPendant.CONFIRM_TIMEOUT + 0.1
    pendant._expire_pending_confirm()

    assert pendant._pending_confirm is None


def test_pending_confirm_survives_until_timeout(monkeypatch):
    pendant = make_pendant()
    fake_now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: fake_now[0])

    hold(pendant, SET)
    tap(pendant, 0)

    fake_now[0] += MacroPadPendant.CONFIRM_TIMEOUT - 0.1
    pendant._expire_pending_confirm()
    assert pendant._pending_confirm == (SET, 0)

    tap(pendant, 0)
    assert ("wcs_set", 0, 0, None, None) in pendant._controller.calls


def test_pressing_a_modifier_clears_a_pending_confirm():
    pendant = make_pendant()

    hold(pendant, SET)
    tap(pendant, 0)
    hold(pendant, GOTO)

    assert pendant._pending_confirm is None


# --- jogging --------------------------------------------------------------------------------


def test_jog_requires_held_axis_key_plus_encoder():
    pendant = make_pendant()

    pendant._handle_encoder_delta(pendant._daemon, 3)  # encoder alone
    assert pendant._controller.calls == []

    hold(pendant, MacroPadPendant.KEY_JOG_Y)
    pendant._handle_encoder_delta(pendant._daemon, 3)
    assert pendant._controller.calls == [("jog", "Y0.3")]


def test_action_modifier_suppresses_jogging():
    """Holding GOTO must not let the encoder jog, even with an axis key also down."""
    pendant = make_pendant()

    hold(pendant, MacroPadPendant.KEY_JOG_X)
    hold(pendant, GOTO)
    pendant._handle_encoder_delta(pendant._daemon, 5)

    assert pendant._controller.calls == []
    assert pendant._held_jog_axis() is None


def test_jog_ignored_when_jogging_disabled():
    pendant = make_pendant(jogging_enabled=False)
    hold(pendant, MacroPadPendant.KEY_JOG_X)

    pendant._handle_encoder_delta(pendant._daemon, 3)

    assert pendant._controller.calls == []


def test_continuous_mode_starts_jog_and_caps_z():
    pendant = make_pendant(jog_mode=Controller.JOG_MODE_CONTINUOUS)
    pendant._step_index = 3  # 10.0mm -> 100% of max speed, but Z is capped
    hold(pendant, MacroPadPendant.KEY_JOG_Z)

    pendant._handle_encoder_delta(pendant._daemon, 1)

    assert pendant._controller.calls == [("start", "Z1", MacroPadPendant.Z_MAX_JOG_SPEED)]
    assert pendant._active_continuous_jog_axis == "Z"


def test_continuous_mode_direction_change_stops_jog():
    pendant = make_pendant(jog_mode=Controller.JOG_MODE_CONTINUOUS)
    hold(pendant, MacroPadPendant.KEY_JOG_X)

    pendant._handle_encoder_delta(pendant._daemon, 1)
    assert pendant._controller.continuous_jog_active is True

    pendant._handle_encoder_delta(pendant._daemon, -1)

    assert ("stop",) in pendant._controller.calls


def test_releasing_jog_key_stops_active_continuous_jog():
    pendant = make_pendant(jog_mode=Controller.JOG_MODE_CONTINUOUS)
    hold(pendant, MacroPadPendant.KEY_JOG_X)
    pendant._handle_encoder_delta(pendant._daemon, 1)
    assert pendant._controller.continuous_jog_active is True

    release(pendant, MacroPadPendant.KEY_JOG_X)

    assert pendant._controller.continuous_jog_active is False
    assert pendant._active_continuous_jog_axis is None
    assert pendant._jog_stops == [True]


def test_encoder_press_cycles_and_wraps_step_index():
    pendant = make_pendant()
    pendant._step_index = 0

    seen = []
    for _ in range(len(MacroPadPendant.STEP_SIZES) + 1):
        pendant._handle_encoder_press(pendant._daemon)
        seen.append(pendant._step_index)

    assert seen == [1, 2, 3, 0, 1]


# --- OLED banner / DRO ----------------------------------------------------------------------


def test_banner_shows_live_coordinate_of_axis_being_jogged():
    pendant = make_pendant(cnc_vars={"state": "Idle", "wx": 12.3456, "wy": -7.0, "wz": 0.0})
    hold(pendant, MacroPadPendant.KEY_JOG_X)

    banner, hint = pendant._display_context()
    assert banner == "X  12.346"
    assert hint == "JOG X  STEP 0.1mm"


def test_jog_banner_tracks_the_held_axis():
    pendant = make_pendant(cnc_vars={"state": "Idle", "wx": 1.0, "wy": -7.0, "wz": 250.5})

    hold(pendant, MacroPadPendant.KEY_JOG_Y)
    assert pendant._display_context()[0] == "Y  -7.000"
    release(pendant, MacroPadPendant.KEY_JOG_Y)

    hold(pendant, MacroPadPendant.KEY_JOG_Z)
    assert pendant._display_context()[0] == "Z 250.500"


def test_jog_banner_updates_as_position_changes():
    pendant = make_pendant(cnc_vars={"state": "Idle", "wx": 0.0})
    hold(pendant, MacroPadPendant.KEY_JOG_X)

    assert pendant._display_context()[0] == "X   0.000"
    pendant._cnc.vars["wx"] = -103.25
    assert pendant._display_context()[0] == "X-103.250"


@pytest.mark.parametrize("value", [0.0, -0.001, 9.5, -9.5, 123.456, -123.456, 999.999, -999.999])
def test_jog_banner_width_is_constant_so_the_oled_scale_never_jumps(value):
    """
    The firmware auto-scales the banner from its length, so a varying-width readout would
    visibly resize while jogging. Every value must render to the same number of characters.
    """
    pendant = make_pendant(cnc_vars={"state": "Idle", "wx": value})
    hold(pendant, MacroPadPendant.KEY_JOG_X)

    banner = pendant._display_context()[0]
    assert len(banner) == MacroPadPendant.JOG_READOUT_WIDTH + 1


def test_jog_banner_hint_fits_the_display_width():
    pendant = make_pendant(cnc_vars={"state": "Idle", "wx": 0.0})
    pendant._step_index = 0  # 0.01mm -- longest step string
    hold(pendant, MacroPadPendant.KEY_JOG_X)

    assert len(pendant._display_context()[1]) <= 21


def test_banner_shows_modifier_name_and_row_mirrored_hints():
    pendant = make_pendant(cnc_vars={"state": "Idle"})

    hold(pendant, GOTO)
    assert pendant._display_context() == ("GOTO", "MHOME SAFEZ WHOME")

    release(pendant, GOTO)
    hold(pendant, ACT)
    banner, hint = pendant._display_context()
    assert banner == "ACT"
    # Two lines mirroring key rows 1 and 2, each within one 21-col display line.
    assert hint == "RUN STOP SPIN|PROBE MAC1"
    assert all(len(line) <= 21 for line in hint.split("|"))


def test_hint_dedupes_a_label_bound_to_two_keys():
    pendant = make_pendant(cnc_vars={"state": "Idle"})
    hold(pendant, SET)

    _banner, hint = pendant._display_context()
    assert hint == "ZEROXY ZEROZ"  # not "ZEROXY ZEROXY ZEROZ"


def test_banner_shows_confirm_prompt():
    pendant = make_pendant(cnc_vars={"state": "Idle"})

    hold(pendant, SET)
    tap(pendant, 0)

    assert pendant._display_context() == ("ZERO XY?", "PRESS AGAIN TO CONFIRM")


def test_alarm_state_overrides_banner():
    pendant = make_pendant(cnc_vars={"state": "Alarm"})
    hold(pendant, GOTO)

    assert pendant._display_context() == ("ALARM", "UNLOCK IN APP")


def test_display_context_is_none_when_idle():
    pendant = make_pendant(cnc_vars={"state": "Idle"})

    assert pendant._display_context() is None


def test_executed_action_flashes_then_returns_to_dro(monkeypatch):
    pendant = make_pendant(cnc_vars={"state": "Idle"})
    fake_now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: fake_now[0])

    hold(pendant, GOTO)
    tap(pendant, 7)
    release(pendant, GOTO)

    assert pendant._display_context() == ("SAFE Z", "")

    fake_now[0] += MacroPadPendant.FLASH_DURATION + 0.1
    assert pendant._display_context() is None


def test_refresh_display_sends_banner_and_hint_on_fw2():
    pendant = make_pendant(cnc_vars={"state": "Idle"})
    hold(pendant, GOTO)

    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.banner_calls == ["GOTO"]
    assert pendant._daemon.hint_calls == ["MHOME SAFEZ WHOME"]


def test_refresh_display_falls_back_to_text_rows_on_fw1():
    pendant = make_pendant(cnc_vars={"state": "Idle"})
    pendant._daemon.firmware_version = 1
    hold(pendant, GOTO)

    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.banner_calls == []
    assert (0, "GOTO") in pendant._daemon.text_calls
    assert (1, "MHOME SAFEZ WHOME") in pendant._daemon.text_calls


def test_refresh_display_exits_banner_mode_when_returning_to_dro():
    pendant = make_pendant(cnc_vars={"wx": 1.0, "wy": 0, "wz": 0, "wa": 0, "state": "Idle"})
    hold(pendant, GOTO)
    pendant._refresh_display(pendant._daemon)
    assert pendant._daemon.banner_calls == ["GOTO"]

    release(pendant, GOTO)
    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.banner_calls == ["GOTO", ""]  # "" leaves banner mode
    assert (0, "X 1.000") in pendant._daemon.text_calls


def test_refresh_display_noop_before_id_handshake():
    pendant = make_pendant()
    pendant._daemon.num_rows = 0

    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.text_calls == []
    assert pendant._daemon.banner_calls == []


def test_dro_rows_formatting():
    pendant = make_pendant(
        cnc_vars={"wx": 1.0, "wy": -2.5, "wz": 0.125, "wa": 0.0, "curfeed": 1200, "curspindle": 10000, "state": "Idle"}
    )

    pendant._refresh_dro(pendant._daemon)

    rows = dict(pendant._daemon.text_calls)
    assert rows[0] == "X 1.000"
    assert rows[1] == "Y -2.500"
    assert rows[2] == "Z 0.125"
    assert rows[3] == "A 0.000"
    assert rows[4] == "F1200 S10000"
    assert rows[5] == "Idle step=0.1mm"


def test_dro_dedupes_unchanged_rows():
    pendant = make_pendant(
        cnc_vars={"wx": 1.0, "wy": 0, "wz": 0, "wa": 0, "curfeed": 0, "curspindle": 0, "state": "Idle"}
    )

    pendant._refresh_dro(pendant._daemon)
    first = len(pendant._daemon.text_calls)
    assert first == 6

    pendant._refresh_dro(pendant._daemon)
    assert len(pendant._daemon.text_calls) == first

    pendant._cnc.vars["wx"] = 2.0
    pendant._refresh_dro(pendant._daemon)
    assert pendant._daemon.text_calls[-1] == (0, "X 2.000")


# --- _safe_number ---------------------------------------------------------------------------


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


# --- LEDs -----------------------------------------------------------------------------------


def test_leds_alarm_overrides_everything():
    pendant = make_pendant(cnc_vars={"state": "Alarm"})

    pendant._refresh_leds(pendant._daemon)

    assert len(pendant._daemon.led_calls) == 12
    assert all(color == 0xFF0000 for _key, color in pendant._daemon.led_calls)


def test_leds_idle_shows_jog_axes_and_modifiers_only():
    pendant = make_pendant(cnc_vars={"state": "Idle"})

    pendant._refresh_leds(pendant._daemon)
    colors = dict(pendant._daemon.led_calls)

    assert colors[0] == MacroPadPendant.AXIS_COLOR_IDLE["X"]
    assert colors[1] == MacroPadPendant.AXIS_COLOR_IDLE["Y"]
    assert colors[2] == MacroPadPendant.AXIS_COLOR_IDLE["Z"]
    for key in MacroPadPendant.MODIFIER_KEYS:
        assert colors[key] == MacroPadPendant.MODIFIER_IDLE_COLOR
    for key in (3, 4, 5, 6, 7, 8):  # targets are dark until a modifier is held
        assert colors[key] == 0x000000


def test_leds_light_only_valid_targets_while_modifier_held():
    pendant = make_pendant(cnc_vars={"state": "Idle"})
    hold(pendant, GOTO)

    pendant._refresh_leds(pendant._daemon)
    colors = dict(pendant._daemon.led_calls)

    assert colors[GOTO] == MacroPadPendant.MODIFIER_COLORS[GOTO]
    for key in (6, 7, 8):
        assert colors[key] == MacroPadPendant.TARGET_COLORS[GOTO]
    # Keys that do nothing under GOTO go dark, including the other modifiers.
    for key in (0, 1, 2, 3, 4, 5, ACT, SET):
        assert colors[key] == 0x000000


def test_leds_highlight_only_the_pending_confirm_target():
    pendant = make_pendant(cnc_vars={"state": "Idle"})
    hold(pendant, SET)
    tap(pendant, 0)

    pendant._refresh_leds(pendant._daemon)
    colors = dict(pendant._daemon.led_calls)

    assert colors[0] == MacroPadPendant.CONFIRM_COLOR
    assert colors[SET] == MacroPadPendant.MODIFIER_COLORS[SET]
    for key in (1, 2, 3, 4, 5, 6, 7, 8, GOTO, ACT):
        assert colors[key] == 0x000000


def test_leds_highlight_held_jog_axis():
    pendant = make_pendant(cnc_vars={"state": "Idle"})
    hold(pendant, MacroPadPendant.KEY_JOG_Y)

    pendant._refresh_leds(pendant._daemon)
    colors = dict(pendant._daemon.led_calls)

    assert colors[1] == MacroPadPendant.AXIS_COLOR_HELD["Y"]
    assert colors[0] == MacroPadPendant.AXIS_COLOR_IDLE["X"]


def test_leds_dedupe_unchanged():
    pendant = make_pendant(cnc_vars={"state": "Idle"})

    pendant._refresh_leds(pendant._daemon)
    first = len(pendant._daemon.led_calls)
    assert first == 12

    pendant._refresh_leds(pendant._daemon)
    assert len(pendant._daemon.led_calls) == first


# --- connect lifecycle ------------------------------------------------------------------------


def test_handle_connect_resets_caches_sends_brightness_and_reports():
    pendant = make_pendant()
    pendant._led_cache = {0: 0x123456}
    pendant._text_cache = {0: "stale"}
    pendant._banner_cache = ("stale", "stale")
    reported = []
    pendant._report_connection = lambda: reported.append(True)

    pendant._handle_connect(pendant._daemon)

    assert pendant._led_cache == {}
    assert pendant._text_cache == {}
    assert pendant._banner_cache == ("", "")
    assert pendant._daemon.brightness_calls == [30]
    assert reported == [True]

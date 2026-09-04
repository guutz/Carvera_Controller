"""Tests for MacroPadPendant: the steno-style chord engine and its feedback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from carveracontroller.addons.pendant import pendant as pendant_module
from carveracontroller.Controller import Controller

MacroPadPendant = pendant_module.MacroPadPendant

GOTO = MacroPadPendant.KEY_GOTO
ACT = MacroPadPendant.KEY_ACT
SET = MacroPadPendant.KEY_SET
JOG_X = MacroPadPendant.KEY_JOG_X
JOG_Y = MacroPadPendant.KEY_JOG_Y
JOG_Z = MacroPadPendant.KEY_JOG_Z


class FakeDaemon:
    def __init__(self, num_rows=6, num_cols=21, firmware_version=3):
        self.pressed_keys: set[int] = set()
        self.num_rows = num_rows
        self.num_cols = num_cols
        self.firmware_version = firmware_version
        self.led_calls: list[tuple[int, int]] = []
        self.text_calls: list[tuple[int, str]] = []
        self.banner_calls: list[str] = []
        self.hint_calls: list[str] = []
        self.brightness_calls: list[int] = []
        self.led_anim_calls: list[tuple[int, str, int, int]] = []
        self.saver_calls: list[int | None] = []

    @property
    def supports_banner(self) -> bool:
        return self.firmware_version >= 2

    @property
    def supports_animation(self) -> bool:
        return self.firmware_version >= 3

    def set_led_anim(self, key: int, mode: str, rgb: int, period_ms: int) -> None:
        self.led_anim_calls.append((key, mode, rgb, period_ms))

    def set_screensaver(self, period_ms: int) -> None:
        self.saver_calls.append(period_ms)

    def clear_screensaver(self) -> None:
        self.saver_calls.append(None)

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

    def setProbeLaser(self, on):
        self.calls.append(("probe_laser", on))

    def autoCommand(self, margin=False, **kwargs):
        self.calls.append(("margin", margin))


LOADED_FILE = {
    "xmin": -50.0,
    "xmax": 50.0,
    "ymin": -25.0,
    "ymax": 25.0,
    "worksize_x": 340.0,
    "worksize_y": 240.0,
}


def make_pendant(cnc_vars=None, jog_mode=Controller.JOG_MODE_STEP, jogging_enabled=True) -> MacroPadPendant:
    pendant = MacroPadPendant.__new__(MacroPadPendant)
    # Most tests care about chord behaviour, not state gating, so default to a machine
    # that can act. Gating tests override "state" explicitly.
    pendant._cnc = SimpleNamespace(vars={"state": "Idle", **(cnc_vars or {})})
    pendant._controller = FakeController(jog_mode=jog_mode)
    pendant._daemon = FakeDaemon()
    pendant._step_index = 1  # 0.1mm
    pendant._last_jog_direction = {}
    pendant._active_continuous_jog_axis = None
    pendant._led_cache = {}
    pendant._text_cache = {}
    pendant._banner_cache = ("", "")
    pendant._stroke_keys = set()
    pendant._stroke_jogged = False
    pendant._pending_confirm = None
    pendant._pending_deadline = 0.0
    pendant._flash_label = ""
    pendant._flash_until = 0.0
    pendant._probe_laser_on = False
    pendant._idle_since = pendant_module.time.monotonic()
    pendant._saver_cache = None
    pendant._max_jog_speed = MacroPadPendant.DEFAULT_MAX_JOG_SPEED
    pendant._brightness = 30
    pendant._show_a_axis = False
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


def press(pendant: MacroPadPendant, key: int) -> None:
    pendant._daemon.pressed_keys.add(key)
    pendant._handle_key_press(pendant._daemon, key)


def lift(pendant: MacroPadPendant, key: int) -> None:
    pendant._daemon.pressed_keys.discard(key)
    pendant._handle_key_release(pendant._daemon, key)


def stroke(pendant: MacroPadPendant, *keys: int) -> None:
    """One complete steno stroke: press every key, then release every key."""
    for key in keys:
        press(pendant, key)
    for key in keys:
        lift(pendant, key)


# --- stroke mechanics -------------------------------------------------------------------


def test_nothing_fires_until_the_last_key_is_released():
    pendant = make_pendant()

    press(pendant, GOTO)
    press(pendant, 7)
    assert pendant._controller.calls == []  # both down, nothing yet

    lift(pendant, 7)
    assert pendant._controller.calls == []  # GOTO still down, stroke unfinished

    lift(pendant, GOTO)
    assert ("safe_z",) in pendant._controller.calls


@pytest.mark.parametrize("order", [(GOTO, 7), (7, GOTO)])
def test_press_order_does_not_matter(order):
    pendant = make_pendant()

    stroke(pendant, *order)

    assert ("safe_z",) in pendant._controller.calls


@pytest.mark.parametrize("lift_order", [(GOTO, 7), (7, GOTO)])
def test_release_order_does_not_matter(lift_order):
    pendant = make_pendant()

    press(pendant, GOTO)
    press(pendant, 7)
    for key in lift_order:
        lift(pendant, key)

    assert ("safe_z",) in pendant._controller.calls


def test_a_key_released_early_still_counts_toward_the_chord():
    """
    The exact case that used to misfire: lifting the modifier before the target. The stroke
    accumulates, so the chord is still {GOTO, SAFE-Z} when the last key comes up.
    """
    pendant = make_pendant()

    press(pendant, GOTO)
    press(pendant, 7)
    lift(pendant, GOTO)  # modifier goes first
    assert pendant._controller.calls == []

    lift(pendant, 7)
    assert ("safe_z",) in pendant._controller.calls


def test_a_brushed_extra_key_changes_the_chord_rather_than_being_ignored():
    """Accumulation cuts both ways: a stray key makes the set spell nothing, so nothing runs."""
    pendant = make_pendant()

    stroke(pendant, GOTO, 7, 4)

    assert pendant._controller.calls == []


def test_unrecognised_chord_does_nothing_and_says_so():
    pendant = make_pendant()

    stroke(pendant, 4, 5)

    assert pendant._controller.calls == []
    assert pendant._flash_label == "NO CHORD"


def test_single_key_alone_is_not_a_chord():
    pendant = make_pendant()

    for key in range(12):
        stroke(pendant, key)

    assert pendant._controller.calls == []


def test_strokes_do_not_leak_into_each_other():
    pendant = make_pendant()

    stroke(pendant, GOTO, 6)
    stroke(pendant, ACT, 4)

    assert ("m_home",) in pendant._controller.calls
    assert ("abort",) in pendant._controller.calls
    assert pendant._stroke_keys == set()


# --- chord vocabulary --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chord", "expected_call", "expected_action"),
    [
        ((GOTO, 3), ("margin", True), "margin"),
        ((GOTO, 6), ("m_home",), "m_home"),
        ((GOTO, 7), ("safe_z",), "safe_z"),
        ((GOTO, 8), ("w_home",), "w_home"),
        ((ACT, 3), ("run_pause",), "start_pause"),
        ((ACT, 4), ("abort",), "stop"),
        ((ACT, 5), ("spindle", True), "spindle_on_off"),
        ((ACT, 6), ("probe_z",), "probe_z"),
        ((ACT, 8), ("probe_laser", True), "probe_laser"),
    ],
)
def test_each_chord_fires_its_action(chord, expected_call, expected_action):
    pendant = make_pendant(cnc_vars={"curspindle": 0, **LOADED_FILE})

    stroke(pendant, *chord)

    assert expected_call in pendant._controller.calls
    assert expected_action in pendant._button_presses


def test_macro_chord_runs_macro_1():
    pendant = make_pendant()

    stroke(pendant, ACT, 7)

    assert ("macro", 1) in pendant._controller.calls


def test_shared_key_means_different_things_in_different_chords():
    pendant = make_pendant(cnc_vars=dict(LOADED_FILE))

    stroke(pendant, GOTO, 3)  # margin
    stroke(pendant, ACT, 3)  # run/pause

    assert ("margin", True) in pendant._controller.calls
    assert ("run_pause",) in pendant._controller.calls


# --- jogging ------------------------------------------------------------------------------


def test_encoder_jogs_while_exactly_one_axis_key_is_held():
    pendant = make_pendant()

    press(pendant, JOG_Y)
    pendant._handle_encoder_delta(pendant._daemon, 3)

    assert pendant._controller.calls == [("jog", "Y0.3")]


def test_encoder_alone_does_not_jog():
    pendant = make_pendant()

    pendant._handle_encoder_delta(pendant._daemon, 3)

    assert pendant._controller.calls == []


def test_encoder_does_not_jog_once_a_second_key_joins_the_stroke():
    """Two keys down means a chord is being formed, not a jog."""
    pendant = make_pendant()

    press(pendant, JOG_X)
    press(pendant, SET)
    pendant._handle_encoder_delta(pendant._daemon, 3)

    assert pendant._controller.calls == []


def test_a_stroke_that_jogged_does_not_also_fire_a_chord():
    """
    Guards the nastiest interaction: jog X, brush SET on the way out, and the release would
    otherwise spell "zero XY" and silently move the work origin.
    """
    pendant = make_pendant()

    press(pendant, JOG_X)
    pendant._handle_encoder_delta(pendant._daemon, 1)
    press(pendant, SET)
    lift(pendant, JOG_X)
    lift(pendant, SET)

    assert not any(call[0] == "wcs_set" for call in pendant._controller.calls)


def test_jog_is_ignored_when_jogging_is_disabled():
    pendant = make_pendant(jogging_enabled=False)

    press(pendant, JOG_X)
    pendant._handle_encoder_delta(pendant._daemon, 3)

    assert pendant._controller.calls == []


def test_continuous_mode_starts_jog_and_caps_z():
    pendant = make_pendant(jog_mode=Controller.JOG_MODE_CONTINUOUS)
    pendant._step_index = 3  # 10.0mm -> 100% of max speed, but Z is capped

    press(pendant, JOG_Z)
    pendant._handle_encoder_delta(pendant._daemon, 1)

    assert pendant._controller.calls == [("start", "Z1", MacroPadPendant.Z_MAX_JOG_SPEED)]


def test_continuous_mode_direction_change_stops_first():
    pendant = make_pendant(jog_mode=Controller.JOG_MODE_CONTINUOUS)

    press(pendant, JOG_X)
    pendant._handle_encoder_delta(pendant._daemon, 1)
    pendant._handle_encoder_delta(pendant._daemon, -1)

    assert ("stop",) in pendant._controller.calls


def test_releasing_the_axis_key_stops_a_continuous_jog():
    pendant = make_pendant(jog_mode=Controller.JOG_MODE_CONTINUOUS)

    press(pendant, JOG_X)
    pendant._handle_encoder_delta(pendant._daemon, 1)
    lift(pendant, JOG_X)

    assert pendant._controller.continuous_jog_active is False
    assert pendant._jog_stops == [True]


def test_encoder_press_cycles_step_size():
    pendant = make_pendant()
    pendant._step_index = 0

    seen = []
    for _ in range(len(MacroPadPendant.STEP_SIZES) + 1):
        pendant._handle_encoder_press(pendant._daemon)
        seen.append(pendant._step_index)

    assert seen == [1, 2, 3, 0, 1]


# --- confirmation ---------------------------------------------------------------------------


def test_zeroing_needs_the_same_chord_twice():
    pendant = make_pendant()

    stroke(pendant, SET, 0)
    assert pendant._controller.calls == []
    assert pendant._pending_confirm == frozenset((SET, 0))

    stroke(pendant, SET, 0)
    assert ("wcs_set", 0, 0, None, None) in pendant._controller.calls
    assert pendant._pending_confirm is None


def test_zero_z_needs_the_same_chord_twice():
    pendant = make_pendant()

    stroke(pendant, SET, 2)
    stroke(pendant, SET, 2)

    assert ("wcs_set", None, None, 0, None) in pendant._controller.calls


def test_a_different_chord_in_between_cancels_the_confirmation():
    pendant = make_pendant()

    stroke(pendant, SET, 0)  # arm zero XY
    stroke(pendant, SET, 2)  # different chord -> arms that one instead
    stroke(pendant, SET, 0)  # so this arms again rather than committing

    assert not any(call[0] == "wcs_set" for call in pendant._controller.calls)


def test_confirmation_expires(monkeypatch):
    pendant = make_pendant()
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])

    stroke(pendant, SET, 0)
    now[0] += MacroPadPendant.CONFIRM_TIMEOUT + 0.1
    pendant._expire_pending_confirm()

    assert pendant._pending_confirm is None


def test_encoder_press_cancels_a_pending_confirmation():
    pendant = make_pendant()
    step_before = pendant.current_step_size

    stroke(pendant, SET, 0)
    pendant._handle_encoder_press(pendant._daemon)

    assert pendant._pending_confirm is None
    assert pendant.current_step_size == step_before


# --- machine-state gating ---------------------------------------------------------------------


@pytest.mark.parametrize("chord", [(GOTO, 6), (GOTO, 7), (SET, 2), (ACT, 6)])
def test_movement_chords_are_blocked_when_not_idle(chord):
    pendant = make_pendant(cnc_vars={"state": "Run", **LOADED_FILE})

    stroke(pendant, *chord)

    assert pendant._controller.calls == []
    assert pendant._flash_label == "NEED IDLE"


def test_stop_is_allowed_in_any_state():
    """Abort must never be gated -- it is the one thing you need while running."""
    for state in ("Idle", "Run", "Pause", "Hold", "Alarm", "Tool"):
        pendant = make_pendant(cnc_vars={"state": state})
        stroke(pendant, ACT, 4)
        assert ("abort",) in pendant._controller.calls, state


def test_run_pause_is_allowed_while_running_but_not_while_alarmed():
    running = make_pendant(cnc_vars={"state": "Run"})
    stroke(running, ACT, 3)
    assert ("run_pause",) in running._controller.calls

    alarmed = make_pendant(cnc_vars={"state": "Alarm"})
    stroke(alarmed, ACT, 3)
    assert alarmed._controller.calls == []
    assert alarmed._flash_label == "NOT READY"


def test_blocked_chord_does_not_arm_a_confirmation():
    pendant = make_pendant(cnc_vars={"state": "Run"})

    stroke(pendant, SET, 0)

    assert pendant._pending_confirm is None


# --- OLED preview -------------------------------------------------------------------------------


def test_preview_shows_the_menu_while_only_a_modifier_is_down():
    pendant = make_pendant()

    press(pendant, GOTO)

    assert pendant._display_context() == ("GOTO", "MARGIN|MHOME SAFEZ WHOME")


def test_preview_shows_what_releasing_now_would_do():
    pendant = make_pendant()

    press(pendant, GOTO)
    press(pendant, 7)

    assert pendant._display_context() == ("SAFE Z", "RELEASE TO RUN")


def test_preview_says_why_a_chord_is_blocked_before_you_release():
    pendant = make_pendant(cnc_vars={"state": "Run"})

    press(pendant, GOTO)
    press(pendant, 7)

    assert pendant._display_context() == ("SAFE Z", "NEED IDLE")


def test_preview_reports_a_meaningless_set():
    pendant = make_pendant()

    press(pendant, 4)
    press(pendant, 5)

    assert pendant._display_context() == ("- - -", "NO CHORD")


def test_preview_shows_the_live_coordinate_while_jogging():
    pendant = make_pendant(cnc_vars={"wx": -103.25})

    press(pendant, JOG_X)

    assert pendant._display_context() == ("X-103.250", "JOG X  STEP 0.1mm")


def test_preview_asks_for_a_repeat_when_confirmation_is_pending():
    pendant = make_pendant()

    stroke(pendant, SET, 0)

    assert pendant._display_context() == ("ZERO XY?", "REPEAT CHORD TO CONFIRM")


def test_alarm_overrides_every_preview():
    pendant = make_pendant(cnc_vars={"state": "Alarm"})
    press(pendant, GOTO)

    assert pendant._display_context() == ("ALARM", "UNLOCK IN APP")


def test_preview_is_none_when_idle_so_the_dro_shows():
    pendant = make_pendant()

    assert pendant._display_context() is None


def test_action_name_flashes_briefly_after_firing(monkeypatch):
    pendant = make_pendant()
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])

    stroke(pendant, GOTO, 7)
    assert pendant._display_context() == ("SAFE Z", "")

    now[0] += MacroPadPendant.FLASH_DURATION + 0.1
    assert pendant._display_context() is None


# --- LED preview ----------------------------------------------------------------------------------


def test_every_key_in_the_stroke_is_highlighted_together():
    """
    The switches have no detent, so the lit set is the only way to see what the pad thinks
    you are holding. Every accumulated key must show, including ones already released.
    """
    pendant = make_pendant()

    press(pendant, GOTO)
    press(pendant, 7)
    lift(pendant, GOTO)  # still part of the stroke

    plan = pendant._led_plan()
    assert plan[GOTO][0] == MacroPadPendant.CHORD_READY_COLOR
    assert plan[7][0] == MacroPadPendant.CHORD_READY_COLOR


def test_incomplete_set_pulses_and_a_resolved_one_goes_steady():
    pendant = make_pendant()

    press(pendant, GOTO)
    forming = pendant._led_plan()[GOTO]
    assert forming == (MacroPadPendant.CHORD_FORMING_COLOR, "pulse")

    press(pendant, 7)
    ready = pendant._led_plan()[7]
    assert ready == (MacroPadPendant.CHORD_READY_COLOR, "solid")


def test_blocked_chord_shows_red():
    pendant = make_pendant(cnc_vars={"state": "Run"})

    press(pendant, GOTO)
    press(pendant, 7)

    assert pendant._led_plan()[7] == (MacroPadPendant.CHORD_BLOCKED_COLOR, "blink_fast")


def test_keys_that_would_complete_a_chord_are_offered():
    pendant = make_pendant()

    press(pendant, GOTO)
    plan = pendant._led_plan()

    for key in (3, 6, 7, 8):  # GOTO's targets
        assert plan[key][1] == "glow", key
    for key in (4, 5, ACT, SET):  # nothing GOTO combines with
        assert plan[key][0] == 0x000000, key


def test_idle_shows_modifiers_breathing_and_axes_steady():
    pendant = make_pendant()

    plan = pendant._led_plan()

    for key in pendant._modifier_keys:
        assert plan[key][1] == "breathe_slow"
    assert plan[JOG_X] == (MacroPadPendant.AXIS_COLOR_IDLE["X"], "solid")
    for key in (3, 4, 5, 6, 7, 8):
        assert plan[key][0] == 0x000000


def test_pending_confirmation_blinks_the_chord():
    pendant = make_pendant()

    stroke(pendant, SET, 0)
    plan = pendant._led_plan()

    assert plan[SET] == (MacroPadPendant.CONFIRM_COLOR, "blink_fast")
    assert plan[0] == (MacroPadPendant.CONFIRM_COLOR, "blink_fast")


def test_alarm_strobes_every_key():
    pendant = make_pendant(cnc_vars={"state": "Alarm"})

    plan = pendant._led_plan()

    assert all(entry == (0xFF0000, "strobe") for entry in plan.values())


# --- animation rendering ------------------------------------------------------------------------


def test_solid_renders_unchanged():
    pendant = make_pendant()

    assert pendant._render(0x123456, "solid") == 0x123456


def test_blink_alternates_between_colour_and_off(monkeypatch):
    pendant = make_pendant()
    period = MacroPadPendant.ANIMATION_PERIODS["blink_fast"]

    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: 0.0)
    assert pendant._render(0xFF0000, "blink_fast") == 0xFF0000

    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: period * 0.75)
    assert pendant._render(0xFF0000, "blink_fast") == 0x000000


def test_pulse_stays_between_its_floor_and_full_brightness(monkeypatch):
    pendant = make_pendant()
    period = MacroPadPendant.ANIMATION_PERIODS["pulse"]
    floor = MacroPadPendant.ANIMATION_FLOORS["pulse"]

    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: 0.0)
    trough = pendant._render(0x00FF00, "pulse") >> 8 & 0xFF

    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: period / 2)
    peak = pendant._render(0x00FF00, "pulse") >> 8 & 0xFF

    assert peak == 0xFF
    assert trough == pytest.approx(0xFF * floor, abs=2)
    assert trough < peak


@pytest.mark.parametrize("animation", list(MacroPadPendant.ANIMATION_PERIODS))
def test_every_animation_renders_a_valid_colour_at_any_phase(monkeypatch, animation):
    pendant = make_pendant()
    period = MacroPadPendant.ANIMATION_PERIODS[animation]

    for fraction in (0.0, 0.1, 0.25, 0.5, 0.75, 0.99):
        monkeypatch.setattr(pendant_module.time, "monotonic", lambda f=fraction: period * f)
        color = pendant._render(0x80C0FF, animation)
        assert 0 <= color <= 0xFFFFFF
        for shift in (16, 8, 0):
            assert 0 <= (color >> shift) & 0xFF <= 0xFF


def test_scale_color_clamps_out_of_range_levels():
    assert MacroPadPendant._scale_color(0xFFFFFF, 2.0) == 0xFFFFFF
    assert MacroPadPendant._scale_color(0xFFFFFF, -1.0) == 0x000000


# --- action behaviour retained from before ----------------------------------------------------


def test_spindle_chord_respects_laser_mode():
    pendant = make_pendant(cnc_vars={"curspindle": 0, "lasermode": True})

    stroke(pendant, ACT, 5)

    assert pendant._controller.calls == []


def test_probe_laser_toggles_and_names_the_direction_sent(monkeypatch):
    pendant = make_pendant()
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])

    stroke(pendant, ACT, 8)
    assert ("probe_laser", True) in pendant._controller.calls
    assert pendant._display_context() == ("LASER ON", "")

    now[0] += MacroPadPendant.FLASH_DURATION + 0.1
    stroke(pendant, ACT, 8)
    assert ("probe_laser", False) in pendant._controller.calls
    assert pendant._display_context() == ("LASER OFF", "")


def test_margin_scans_with_a_file_loaded():
    pendant = make_pendant(cnc_vars=dict(LOADED_FILE))

    stroke(pendant, GOTO, 3)

    assert ("margin", True) in pendant._controller.calls


@pytest.mark.parametrize(
    "override",
    [
        {"xmin": 1000000.0, "xmax": -1000000.0},  # nothing loaded
        {"xmax": -50.0},
        {"ymax": -25.0},
        {"xmin": -500.0},
        {"xmin": "nonsense"},
        {"worksize_x": None},
    ],
)
def test_margin_refuses_unusable_bounds(override):
    pendant = make_pendant(cnc_vars={**LOADED_FILE, **override})

    stroke(pendant, GOTO, 3)

    assert pendant._controller.calls == []
    assert pendant._flash_label == "NO FILE"


def test_margin_refuses_in_laser_mode():
    pendant = make_pendant(cnc_vars={**LOADED_FILE, "lasermode": True})

    stroke(pendant, GOTO, 3)

    assert pendant._controller.calls == []
    assert pendant._flash_label == "LASER MODE"


@pytest.mark.parametrize(
    ("value", "lo", "hi", "expected"),
    [
        (0, 0, 100, 0.0),
        (150, 0, 100, 100.0),
        (-5, 0, 100, 0.0),
        (-5, -100, 100, -5.0),
        ("42.5", 0, 100, 42.5),
        ("invalid", 0, 100, 0.0),
        (None, 0, 100, 0.0),
        (float("nan"), 0, 100, 0.0),
        (float("inf"), 0, 100, 0.0),
        pytest.param(10**10000, 0, 100, 100.0, id="huge-positive-int"),
        pytest.param(-(10**10000), -100, 100, -100.0, id="huge-negative-int"),
    ],
)
def test_safe_number(value, lo, hi, expected):
    assert MacroPadPendant._safe_number(value, lo, hi) == expected


# --- display plumbing ---------------------------------------------------------------------------


DRO_VARS = {
    "wx": 1.0,
    "wy": -2.5,
    "wz": 0.125,
    "wa": 7.5,
    "curfeed": 1200,
    "curspindle": 10000,
    "active_coord_system": 0,
    "tool": 1,
    "tlo": -12.345,
}


def test_dro_rows_formatting():
    pendant = make_pendant(cnc_vars=dict(DRO_VARS))

    pendant._refresh_dro(pendant._daemon)

    rows = dict(pendant._daemon.text_calls)
    assert rows[0] == "X 1.000"
    assert rows[1] == "Y -2.500"
    assert rows[2] == "Z 0.125"
    assert rows[3] == "G54 T1 TLO-12.345"
    assert rows[4] == "Idle step=0.1mm"
    assert rows[5] == "F1200 S10000"


def test_dro_omits_the_fourth_axis_by_default():
    pendant = make_pendant(cnc_vars=dict(DRO_VARS))

    pendant._refresh_dro(pendant._daemon)

    assert not any(text.startswith("A ") for _row, text in pendant._daemon.text_calls)


def test_dro_shows_the_fourth_axis_when_enabled_at_the_cost_of_feed_spindle():
    """Six rows only, so the A row displaces the last one."""
    pendant = make_pendant(cnc_vars=dict(DRO_VARS))
    pendant._show_a_axis = True

    pendant._refresh_dro(pendant._daemon)

    rows = dict(pendant._daemon.text_calls)
    assert rows[3] == "A 7.500"
    assert rows[4] == "G54 T1 TLO-12.345"
    assert rows[5] == "Idle step=0.1mm"
    assert not any(text.startswith("F") for text in rows.values())


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0, "G54"), (1, "G55"), (5, "G59"), (6, "G59.1"), (8, "G59.3")],
)
def test_wcs_row_follows_the_active_coordinate_system(index, expected):
    pendant = make_pendant(cnc_vars={**DRO_VARS, "active_coord_system": index})

    assert pendant._wcs_name() == expected


@pytest.mark.parametrize("bad", [99, -1, "x", None])
def test_wcs_name_survives_a_nonsense_index(bad):
    pendant = make_pendant(cnc_vars={**DRO_VARS, "active_coord_system": bad})

    assert pendant._wcs_name() in ("G54", "G??")


@pytest.mark.parametrize(
    ("tool", "expected"),
    [(0, "T0"), (1, "T1"), (7, "T7"), (-1, "T-"), ("x", "T-"), (None, "T-")],
)
def test_tool_text_handles_no_tool_and_junk(tool, expected):
    pendant = make_pendant(cnc_vars={**DRO_VARS, "tool": tool})

    assert pendant._tool_text() == expected


def test_worst_case_wcs_row_still_fits_the_display():
    """G59.3 + a two-digit tool + a large negative offset is the longest this row gets."""
    pendant = make_pendant(cnc_vars={**DRO_VARS, "active_coord_system": 8, "tool": 99, "tlo": -123.456})

    pendant._refresh_dro(pendant._daemon)

    row = dict(pendant._daemon.text_calls)[3]
    assert row == "G59.3 T99 TLO-123.456"
    assert len(row) <= pendant._daemon.num_cols


def test_banner_and_hint_go_out_when_supported():
    pendant = make_pendant()
    press(pendant, GOTO)

    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.banner_calls == ["GOTO"]
    assert pendant._daemon.hint_calls == ["MARGIN|MHOME SAFEZ WHOME"]


def test_fw1_falls_back_to_text_rows():
    pendant = make_pendant()
    pendant._daemon.firmware_version = 1
    press(pendant, GOTO)

    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.banner_calls == []
    assert (0, "GOTO") in pendant._daemon.text_calls


def test_returning_to_idle_leaves_banner_mode():
    pendant = make_pendant(cnc_vars={"wx": 1.0})
    press(pendant, GOTO)
    pendant._refresh_display(pendant._daemon)

    lift(pendant, GOTO)
    pendant._flash_label = ""
    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.banner_calls == ["GOTO", ""]
    assert (0, "X 1.000") in pendant._daemon.text_calls


def test_display_is_a_noop_before_the_id_handshake():
    pendant = make_pendant()
    pendant._daemon.num_rows = 0

    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.text_calls == []
    assert pendant._daemon.banner_calls == []


def test_connect_resets_caches_and_pushes_brightness():
    pendant = make_pendant()
    pendant._led_cache = {0: 0x123456}
    pendant._text_cache = {0: "stale"}
    pendant._banner_cache = ("stale", "stale")
    reported = []
    pendant._report_connection = lambda: reported.append(True)

    pendant._handle_connect(pendant._daemon)

    assert pendant._led_cache == {}
    assert pendant._banner_cache == ("", "")
    assert pendant._daemon.brightness_calls == [30]
    assert reported == [True]


# --- firmware animation offload -----------------------------------------------------------------


def test_animated_keys_are_handed_to_the_firmware():
    """Firmware 3+ renders animations itself, so we send one command, not frames."""
    pendant = make_pendant()

    pendant._refresh_leds(pendant._daemon)

    modifier_anims = [c for c in pendant._daemon.led_anim_calls if c[0] in pendant._modifier_keys]
    assert len(modifier_anims) == 3
    for _key, mode, _rgb, period_ms in modifier_anims:
        assert mode == "breathe"  # firmware's name for breathe_slow
        assert period_ms == int(MacroPadPendant.ANIMATION_PERIODS["breathe_slow"] * 1000)


def test_solid_keys_use_the_plain_led_command_even_on_fw3():
    pendant = make_pendant()

    pendant._refresh_leds(pendant._daemon)

    assert (JOG_X, MacroPadPendant.AXIS_COLOR_IDLE["X"]) in pendant._daemon.led_calls
    assert not any(call[0] == JOG_X for call in pendant._daemon.led_anim_calls)


def test_firmware_animation_names_are_translated():
    pendant = make_pendant(cnc_vars={"state": "Alarm"})

    pendant._refresh_leds(pendant._daemon)

    assert {call[1] for call in pendant._daemon.led_anim_calls} == {"strobe"}


def test_blink_fast_is_sent_as_blink():
    pendant = make_pendant()
    stroke(pendant, SET, 0)  # arms a confirmation -> blink_fast

    pendant._refresh_leds(pendant._daemon)

    modes = {call[1] for call in pendant._daemon.led_anim_calls}
    assert modes == {"blink"}


def test_repeated_refreshes_do_not_resend_unchanged_animations():
    pendant = make_pendant()

    pendant._refresh_leds(pendant._daemon)
    first = len(pendant._daemon.led_anim_calls) + len(pendant._daemon.led_calls)

    pendant._refresh_leds(pendant._daemon)
    assert len(pendant._daemon.led_anim_calls) + len(pendant._daemon.led_calls) == first


def test_older_firmware_gets_host_rendered_colours(monkeypatch):
    pendant = make_pendant()
    pendant._daemon.firmware_version = 2
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: 0.0)

    pendant._refresh_leds(pendant._daemon)

    assert pendant._daemon.led_anim_calls == []
    assert pendant._daemon.led_calls  # plain colours instead


# --- OLED screensaver ----------------------------------------------------------------------------


def rest(pendant, now, seconds=None) -> None:
    """Advance a fake clock past the screensaver delay."""
    pendant._idle_since = now[0]
    now[0] += MacroPadPendant.SCREENSAVER_AFTER + 1 if seconds is None else seconds


def test_screensaver_starts_only_after_a_spell_of_inactivity(monkeypatch):
    pendant = make_pendant()
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    pendant._idle_since = now[0]

    pendant._refresh_display(pendant._daemon)
    assert pendant._daemon.saver_calls == []  # nothing running, nothing to cancel

    now[0] += MacroPadPendant.SCREENSAVER_AFTER + 1
    pendant._refresh_display(pendant._daemon)
    assert pendant._daemon.saver_calls == [MacroPadPendant.SCREENSAVER_PERIOD_MS]


def test_screensaver_blanks_the_display_rather_than_pushing_text(monkeypatch):
    """While the device is animating the dot, sending rows would fight it."""
    pendant = make_pendant(cnc_vars={"wx": 1.0})
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    rest(pendant, now)

    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.text_calls == []
    assert pendant._daemon.banner_calls == []


def test_touching_a_key_wakes_the_display(monkeypatch):
    pendant = make_pendant()
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    rest(pendant, now)
    pendant._refresh_display(pendant._daemon)
    assert pendant._saver_cache is not None

    press(pendant, GOTO)
    pendant._refresh_display(pendant._daemon)

    assert pendant._saver_cache is None
    assert pendant._daemon.saver_calls[-1] is None
    assert pendant._daemon.banner_calls == ["GOTO"]  # and the banner is pushed again


def test_waking_repushes_the_dro_the_device_blanked(monkeypatch):
    pendant = make_pendant(cnc_vars={"wx": 1.0, "wy": 0, "wz": 0, "wa": 0})
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    pendant._refresh_display(pendant._daemon)  # DRO cached
    rest(pendant, now)
    pendant._refresh_display(pendant._daemon)  # sleeps

    pendant._idle_since = now[0]
    pendant._refresh_display(pendant._daemon)  # wakes

    assert (0, "X 1.000") in pendant._daemon.text_calls[-6:]


def test_screensaver_is_not_resent_every_refresh(monkeypatch):
    pendant = make_pendant()
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    rest(pendant, now)

    pendant._refresh_display(pendant._daemon)
    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.saver_calls.count(MacroPadPendant.SCREENSAVER_PERIOD_MS) == 1


def test_leds_keep_working_while_the_screen_sleeps(monkeypatch):
    """Only the display sleeps; the keys stay readable so the pad still looks alive."""
    pendant = make_pendant()
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    rest(pendant, now)

    pendant._refresh_display(pendant._daemon)
    pendant._refresh_leds(pendant._daemon)

    for key in pendant._modifier_keys:
        assert any(call[0] == key for call in pendant._daemon.led_anim_calls)


@pytest.mark.parametrize("state", list(MacroPadPendant.BUSY_STATES))
def test_screensaver_never_hides_a_machine_that_is_doing_something(monkeypatch, state):
    pendant = make_pendant(cnc_vars={"state": state})
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    rest(pendant, now)

    assert pendant._screensaver_period() is None


def test_alarm_beats_the_screensaver(monkeypatch):
    pendant = make_pendant(cnc_vars={"state": "Alarm"})
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    rest(pendant, now)

    assert pendant._screensaver_period() is None


def test_pending_confirmation_beats_the_screensaver(monkeypatch):
    pendant = make_pendant()
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    stroke(pendant, SET, 0)
    rest(pendant, now)

    assert pendant._screensaver_period() is None


def test_activity_keeps_resetting_the_idle_clock(monkeypatch):
    pendant = make_pendant()
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    pendant._idle_since = now[0]

    press(pendant, GOTO)
    now[0] += MacroPadPendant.SCREENSAVER_AFTER + 1
    pendant._handle_display_update(pendant._daemon)

    assert pendant._idle_since == now[0]
    assert pendant._screensaver_period() is None


def test_older_firmware_never_sleeps_the_screen(monkeypatch):
    pendant = make_pendant()
    pendant._daemon.firmware_version = 2
    now = [1000.0]
    monkeypatch.setattr(pendant_module.time, "monotonic", lambda: now[0])
    rest(pendant, now)

    pendant._refresh_display(pendant._daemon)

    assert pendant._daemon.saver_calls == []
    assert pendant._daemon.text_calls  # keeps showing the DRO instead

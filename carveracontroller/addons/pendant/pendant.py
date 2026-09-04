from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Callable
from typing import NamedTuple

logger = logging.getLogger(__name__)

from kivy.app import App
from kivy.clock import Clock
from kivy.config import Config
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.settings import SettingItem
from kivy.uix.spinner import Spinner

from carveracontroller.CNC import CNC
from carveracontroller.Controller import Controller
from carveracontroller.translation import tr

from . import gamepad as gamepad_module


class OverrideController:
    def __init__(
        self,
        get_value: Callable[[], float],
        set_value: Callable[[float], None],
        min_limit: int = 0,
        max_limit: int = 200,
        step: int = 10,
    ) -> None:
        self._get_value = get_value
        self._set_value = set_value
        self._min_limit = min_limit
        self._max_limit = max_limit
        self._step = step

    def on_increase(self) -> None:
        new_value = min(self._get_value() + self._step, self._max_limit)
        self._set_value(new_value)

    def on_decrease(self) -> None:
        new_value = max(self._get_value() - self._step, self._min_limit)
        self._set_value(new_value)


class Pendant:
    """
    Base class for pendant devices.

    The pendant system supports UI updates through callback functions:
    - update_ui_on_button_press: Called when any button is pressed with the button action
    - update_ui_on_jog_stop: Called when jogging stops

    Button actions include:
    - "reset", "stop", "start_pause": Control buttons
    - "mode_continuous", "mode_step": Jog mode buttons
    - "feed_plus", "feed_minus", "spindle_plus", "spindle_minus": Override buttons
    - "m_home", "safe_z", "w_home": Movement buttons
    - "spindle_on_off", "probe_z": Function buttons
    """

    def __init__(
        self,
        controller: Controller,
        cnc: CNC,
        feed_override: OverrideController,
        spindle_override: OverrideController,
        is_jogging_enabled: Callable[[], None],
        handle_run_pause_resume: Callable[[], None],
        handle_probe_z: Callable[[], None],
        open_probing_popup: Callable[[], None],
        report_connection: Callable[[], None],
        report_disconnection: Callable[[], None],
        update_ui_on_button_press: Callable[[str], None] = None,
        update_ui_on_jog_stop: Callable[[], None] = None,
    ) -> None:
        self._controller = controller
        self._cnc = cnc
        self._feed_override = feed_override
        self._spindle_override = spindle_override

        self._is_jogging_enabled = is_jogging_enabled
        self._handle_run_pause_resume = handle_run_pause_resume
        self._handle_probe_z = handle_probe_z
        self._open_probing_popup = open_probing_popup
        self._report_connection = report_connection
        self._report_disconnection = report_disconnection
        self._update_ui_on_button_press = update_ui_on_button_press
        self._update_ui_on_jog_stop = update_ui_on_jog_stop
        self._jog_mode = Controller.JOG_MODE_STEP

    def close(self) -> None:
        pass

    def executor(self, f: Callable[[], None]) -> None:
        Clock.schedule_once(lambda _: f(), 0)

    def run_macro(self, macro_id: int) -> None:
        macro_key = f"pendant_macro_{macro_id}"
        macro_value = Config.get("carvera", macro_key)

        if not macro_value:
            logger.warning(f"No macro defined for ID {macro_id}")
            return

        macro_value = json.loads(macro_value)

        try:
            lines = macro_value.get("gcode", "").splitlines()
            for l in lines:
                l = l.strip()
                if l == "":
                    continue
                self._controller.sendGCode(l)
        except Exception as e:
            logger.error(f"Failed to run macro {macro_id}: {e}")


class NonePendant(Pendant):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)


try:
    from . import whb04

    WHB04_SUPPORTED = True
except Exception as e:
    logger.warning(f"WHB04 pendant not supported: {e}")
    WHB04_SUPPORTED = False

if WHB04_SUPPORTED:

    class WHB04(Pendant):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)

            self._is_spindle_running = False
            self._last_jog_direction = (
                0  # Track previous jog direction (0 = no direction, positive = CW, negative = CCW)
            )

            self._daemon = whb04.Daemon(self.executor)

            self._daemon.on_connect = self._handle_connect
            self._daemon.on_disconnect = self._handle_disconnect
            self._daemon.on_update = self._handle_display_update
            self._daemon.on_jog = self._handle_jogging
            self._daemon.on_button_press = self._handle_button_press
            self._daemon.on_stop_jog = self._handle_stop_jog
            self._daemon.on_permission_error = self._handle_permission_error

            self._daemon.start()

        def _handle_connect(self, daemon: whb04.Daemon) -> None:
            daemon.set_display_step_indicator(whb04.StepIndicator.STEP)
            self._report_connection()

        def _handle_disconnect(self, daemon: whb04.Daemon) -> None:
            self._report_disconnection()

        def _handle_display_update(self, daemon: whb04.Daemon) -> None:
            daemon.set_display_position(whb04.Axis.X, self._cnc.vars["wx"])
            daemon.set_display_position(whb04.Axis.Y, self._cnc.vars["wy"])
            daemon.set_display_position(whb04.Axis.Z, self._cnc.vars["wz"])
            daemon.set_display_position(whb04.Axis.A, self._cnc.vars["wa"])
            # daemon.set_display_feedrate(self._cnc.vars["curfeed"])
            # rbeard-ewa fix unhandled exception for bad feedrate
            try:
                raw_feed = float(self._cnc.vars.get("curfeed", 0))
                safe_feed = max(0.0, min(raw_feed, 9999.0))
                daemon.set_display_feedrate(safe_feed)
            except ValueError:
                pass
            # end of feedrate fix
            spindle_value = self._cnc.vars.get("curspindle", 0)
            try:
                raw_spindle = float(spindle_value)
            except OverflowError:
                # A finite integer can exceed float's range; clamp it by sign.
                raw_spindle = 65535.0 if spindle_value > 0 else 0.0
            except (TypeError, ValueError):
                raw_spindle = 0.0
            if not math.isfinite(raw_spindle):
                raw_spindle = 0.0
            safe_spindle = max(0.0, min(raw_spindle, 65535.0))
            daemon.set_display_spindle_speed(safe_spindle)

            # Update the step indicator to reflect current jog mode
            if self._controller.jog_mode == self._controller.JOG_MODE_CONTINUOUS:
                self._jog_mode = self._controller.JOG_MODE_CONTINUOUS
                daemon.set_display_step_indicator(whb04.StepIndicator.CONTINUOUS)
            else:
                self._jog_mode = self._controller.JOG_MODE_STEP
                daemon.set_display_step_indicator(whb04.StepIndicator.STEP)

        def _handle_jogging(self, daemon: whb04.Daemon, steps: int) -> None:
            if not self._is_jogging_enabled() and not self._controller.continuous_jog_active:
                return

            axis = daemon.active_axis_name

            if axis not in "XYZA":
                return

            if self._controller.jog_mode != self._jog_mode:
                self._controller.jog_mode = self._jog_mode

            # Detect direction change for continuous jog
            if self._controller.jog_mode == self._controller.JOG_MODE_CONTINUOUS:
                # Determine current direction (positive = CW, negative = CCW)
                current_direction = 1 if steps > 0 else (-1 if steps < 0 else 0)

                # Check if direction has changed and continuous jog is active
                if (
                    self._last_jog_direction != 0
                    and current_direction != 0
                    and self._last_jog_direction != current_direction
                    and self._controller.continuous_jog_active
                ):
                    self._controller.stopContinuousJog()

                # Update direction tracking
                if current_direction != 0:
                    self._last_jog_direction = current_direction

                distance = steps
                feed = self._controller.jog_speed * daemon.step_size_value
            else:
                # Reset direction tracking for step mode
                self._last_jog_direction = 0
                distance = steps * daemon.step_size_value

            # Jog as fast as you can as the machine should follow the pendant as
            # closely as possible. We choose some reasonably high speed here,
            # the machine will limit itself to the maximum speed it can handle.
            if self._controller.jog_mode == self._controller.JOG_MODE_CONTINUOUS:
                if not self._controller.continuous_jog_active:
                    if feed > 0 and self._controller.jog_speed < 10000:
                        if axis == "Z":
                            feed = min(800 * daemon.step_size_value, feed)
                        self._controller.startContinuousJog(f"{axis}{distance}", feed)
                    elif feed == 0 or self._controller.jog_speed == 10000:
                        if axis == "Z":
                            self._controller.startContinuousJog(f"{axis}{distance}", 800 * daemon.step_size_value)
                        else:
                            self._controller.startContinuousJog(f"{axis}{distance}", None, f"S{daemon.step_size_value}")
            else:
                if daemon.step_size == whb04.StepSize.LEAD:
                    self._controller.jog(
                        f"{axis}{round(steps * 0.1, 3)}", round(abs(steps * 0.1 / 0.05) * 60 * 0.97, 3)
                    )
                else:
                    self._controller.jog(f"{axis}{round(distance, 3)}")

        def _handle_button_press(self, daemon: whb04.Daemon, button: whb04.Button) -> None:
            is_fn_pressed = whb04.Button.FN in daemon.pressed_buttons
            is_action_primary = Config.get("carvera", "pendant_primary_button_action") == "Key-specific Action"

            should_run_action = is_fn_pressed
            if is_action_primary:
                should_run_action = not should_run_action

            if button == whb04.Button.RESET:
                self._controller.estopCommand()
                if self._update_ui_on_button_press:
                    self._update_ui_on_button_press("reset")
            if button == whb04.Button.STOP:
                self._controller.abortCommand()
                if self._update_ui_on_button_press:
                    self._update_ui_on_button_press("stop")
            if button == whb04.Button.START_PAUSE:
                self._handle_run_pause_resume()
                if self._update_ui_on_button_press:
                    self._update_ui_on_button_press("start_pause")

            # Handle jog mode switching buttons (these work regardless of FN state)
            if button == whb04.Button.MODE_CONTINUOUS:
                if not self._controller.is_community_firmware:
                    return
                self._controller.setJogMode(self._controller.JOG_MODE_CONTINUOUS)
                self._jog_mode = self._controller.JOG_MODE_CONTINUOUS
                if self._update_ui_on_button_press:
                    self._update_ui_on_button_press("mode_continuous")
            if button == whb04.Button.MODE_STEP:
                self._controller.setJogMode(Controller.JOG_MODE_STEP)
                self._jog_mode = Controller.JOG_MODE_STEP
                if self._update_ui_on_button_press:
                    self._update_ui_on_button_press("mode_step")

            if should_run_action:
                if button == whb04.Button.FEED_PLUS:
                    self._feed_override.on_increase()
                    if self._update_ui_on_button_press:
                        self._update_ui_on_button_press("feed_plus")
                if button == whb04.Button.FEED_MINUS:
                    self._feed_override.on_decrease()
                    if self._update_ui_on_button_press:
                        self._update_ui_on_button_press("feed_minus")
                if button == whb04.Button.SPINDLE_PLUS:
                    self._spindle_override.on_increase()
                    if self._update_ui_on_button_press:
                        self._update_ui_on_button_press("spindle_plus")
                if button == whb04.Button.SPINDLE_MINUS:
                    self._spindle_override.on_decrease()
                    if self._update_ui_on_button_press:
                        self._update_ui_on_button_press("spindle_minus")
                if button == whb04.Button.M_HOME:
                    self._controller.gotoMachineHome()
                    if self._update_ui_on_button_press:
                        self._update_ui_on_button_press("m_home")
                if button == whb04.Button.SAFE_Z:
                    self._controller.gotoSafeZ()
                    if self._update_ui_on_button_press:
                        self._update_ui_on_button_press("safe_z")
                if button == whb04.Button.W_HOME:
                    self._controller.gotoWCSHome()
                    if self._update_ui_on_button_press:
                        self._update_ui_on_button_press("w_home")
                if button == whb04.Button.S_ON_OFF:
                    self._is_spindle_running = not self._is_spindle_running
                    self._controller.setSpindleSwitch(self._is_spindle_running)
                    if self._update_ui_on_button_press:
                        self._update_ui_on_button_press("spindle_on_off")
                if button == whb04.Button.PROBE_Z:
                    self._handle_probe_z()
                    if self._update_ui_on_button_press:
                        self._update_ui_on_button_press("probe_z")
                if button == whb04.Button.MACRO_10:  # macro-10 has no action so it should always run
                    self.run_macro(10)
            else:
                MACROS = [
                    whb04.Button.FEED_PLUS,
                    whb04.Button.FEED_MINUS,
                    whb04.Button.SPINDLE_PLUS,
                    whb04.Button.SPINDLE_MINUS,
                    whb04.Button.M_HOME,
                    whb04.Button.SAFE_Z,
                    whb04.Button.W_HOME,
                    whb04.Button.S_ON_OFF,
                    whb04.Button.PROBE_Z,
                    whb04.Button.MACRO_10,
                ]
                if button not in MACROS:
                    return
                macro_idx = 1 + MACROS.index(button)
                self.run_macro(macro_idx)

        def _handle_stop_jog(self, daemon: whb04.Daemon) -> None:
            if self._controller.continuous_jog_active:
                self._controller.stopContinuousJog()
                if self._update_ui_on_jog_stop:
                    self._update_ui_on_jog_stop()

        def _handle_permission_error(self, daemon: whb04.Daemon) -> None:
            message = (
                "The WHB04 pendant was found but cannot be opened due to\n"
                "insufficient permissions on the USB HID device.\n\n"
                "See the documentation on how to fix these permissions on Linux:\n"
                "https://carvera-community.gitbook.io/docs/controller/features/pendant-support#linux"
            )
            print(f"\nERROR: {message}\n", flush=True)

            scroll = ScrollView(size_hint=(1, 1))
            lbl = Label(text=message, halign="left", valign="top", size_hint_y=None)
            lbl.bind(
                width=lambda w, v: setattr(w, "text_size", (v, None)),
                texture_size=lambda w, v: setattr(w, "height", v[1]),
            )
            scroll.add_widget(lbl)

            btn = Button(text="Exit", size_hint_y=None, height=dp(44))

            content = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
            content.add_widget(scroll)
            content.add_widget(btn)

            popup = Popup(
                title="Pendant Permission Error",
                content=content,
                size_hint=(0.85, 0.65),
                auto_dismiss=False,
            )
            btn.bind(on_release=lambda *_: App.get_running_app().stop())
            popup.open()


class _PendantTarget(NamedTuple):
    """One action, reached by a chord of a modifier key plus a target key."""

    label: str  # shown large on the OLED ("what am I about to do")
    hint: str  # compact form for the on-screen legend of available targets
    action: Callable[[], None]
    needs_confirm: bool = False
    action_name: str = ""  # registry key, used to look up machine-state requirements


try:
    from . import macropad

    MACROPAD_SUPPORTED = True
except Exception as e:
    logger.warning(f"MacroPad pendant not supported: {e}")
    MACROPAD_SUPPORTED = False


if MACROPAD_SUPPORTED:

    class MacroPadPendant(Pendant):
        """
        Driver for the Adafruit MacroPad RP2040 pendant. Talks the application-agnostic
        serial protocol implemented by the firmware in the sibling `macropad_pendant/`
        project (see its PROTOCOL.md) -- this class is where all the CNC-specific meaning
        gets attached to raw key/encoder events.

        Every action requires two controls at once -- no single keypress ever moves the
        machine. There are two chord shapes:

          * hold a jog key (row 0) and turn the encoder to jog that axis
          * hold a modifier key (row 3) and press one of its target keys

        Layout (3x4 key grid, numbered left-to-right/top-to-bottom):

            Row 0:  0 X          |  1 Y          |  2 Z         <- jog holds / SET targets
            Row 1:  3 run/pause  |  4 stop       |  5 spindle   <- ACT targets
                    (3 margin scan under GOTO)
            Row 2:  6 m-home     |  7 safe Z     |  8 w-home    <- GOTO targets
                    (6 probe Z, 7 macro 1, 8 probe laser under ACT)
            Row 3:  9 GOTO       | 10 ACT        | 11 SET       <- modifiers

        So: hold GOTO + press 7 = go to safe Z. Hold ACT + press 3 = run/pause. Hold SET +
        press 0/1 = zero XY (needs confirming), + press 2 = zero Z.

        Targets marked needs_confirm show "<LABEL>?" on the OLED and only fire when the same
        key is pressed a second time; the encoder press, releasing the modifier, or
        CONFIRM_TIMEOUT elapsing all cancel. Holding two modifiers uses the most recently
        pressed one.

        The OLED shows the DRO when nothing is engaged, and switches to a big banner naming
        the pending action the moment a jog key or modifier goes down. NeoPixels light only
        the keys that are actually live in the current mode, so the layout is discoverable
        without memorizing it.

        The layout is data, not code: modifiers, jog keys and every binding come from
        macropad_layout.json (shipped alongside this module; a copy in the app's config
        directory overrides it). See _action_specs for the bindable action names and
        macropad_pendant/README.md for the file format.
        """

        DEFAULT_MAX_JOG_SPEED = 3000  # mm/min, continuous mode only
        Z_MAX_JOG_SPEED = 800  # mm/min cap for Z in continuous mode
        STEP_SIZES = [0.01, 0.1, 1.0, 10.0]
        STEP_SIZE_SPEED_FRACTION = {0.01: 0.02, 0.1: 0.10, 1.0: 0.30, 10.0: 1.0}

        # Width of the numeric field in the jog readout banner. 8 covers -999.999..999.999;
        # with the axis letter that's 9 chars, which the firmware renders at a fixed scale.
        JOG_READOUT_WIDTH = 8

        CONFIRM_TIMEOUT = 4.0  # seconds an unconfirmed prompt stays live
        FLASH_DURATION = 0.9  # seconds an executed action's name stays on screen
        MAX_HINT_LEN = 45  # two ~21-col lines plus the "|" separator

        # Key numbers of the shipped default layout. The live layout comes from
        # macropad_layout.json (see _load_layout); these name the defaults for readability
        # and are what the bundled layout is checked against in tests.
        KEY_JOG_X = 0
        KEY_JOG_Y = 1
        KEY_JOG_Z = 2
        KEY_GOTO = 9
        KEY_ACT = 10
        KEY_SET = 11

        LAYOUT_FILENAME = "macropad_layout.json"

        AXIS_COLOR_IDLE = {"X": 0x200000, "Y": 0x002000, "Z": 0x000020}
        AXIS_COLOR_HELD = {"X": 0xFF0000, "Y": 0x00FF00, "Z": 0x0000FF}

        # Machine states each action needs. Absent = always allowed. Kept here rather than
        # in the layout file: it is a property of the action, not of where you bind it.
        IDLE_ONLY = ("Idle",)
        ACTION_REQUIRES = {
            "margin": IDLE_ONLY,
            "machine_home": IDLE_ONLY,
            "safe_z": IDLE_ONLY,
            "work_home": IDLE_ONLY,
            "probe_z": IDLE_ONLY,
            "zero_xy": IDLE_ONLY,
            "zero_z": IDLE_ONLY,
            "spindle_toggle": ("Idle", "Run", "Pause", "Hold"),
            "run_pause": ("Idle", "Run", "Pause", "Hold"),
        }
        STATE_HINTS = {
            "margin": "NEED IDLE",
            "machine_home": "NEED IDLE",
            "safe_z": "NEED IDLE",
            "work_home": "NEED IDLE",
            "probe_z": "NEED IDLE",
            "zero_xy": "NEED IDLE",
            "zero_z": "NEED IDLE",
            "spindle_toggle": "NOT READY",
            "run_pause": "NOT READY",
        }

        MODIFIER_IDLE_COLOR = 0x0A0A0A
        # Keyed by modifier *name*, so renaming or moving a modifier in the layout file
        # keeps its colour. Unknown names fall back to the default pair.
        MODIFIER_COLORS = {"GOTO": 0x0060FF, "ACT": 0xFF6000, "SET": 0xFF00C0}
        TARGET_COLORS = {"GOTO": 0x001830, "ACT": 0x301400, "SET": 0x300024}
        DEFAULT_MODIFIER_COLOR = 0x808080
        DEFAULT_TARGET_COLOR = 0x181818
        CONFIRM_COLOR = 0xFFFF00

        # Stroke preview. White while the set spells nothing yet, green once it resolves to
        # something that can run, red when it resolves to something blocked by machine state.
        CHORD_FORMING_COLOR = 0xFFFFFF
        CHORD_READY_COLOR = 0x00FF40
        CHORD_BLOCKED_COLOR = 0xFF0000

        # The firmware's mode vocabulary; ours is a little more descriptive.
        FIRMWARE_ANIMATION_NAMES = {"breathe_slow": "breathe", "blink_fast": "blink"}

        ANIMATION_PERIODS = {
            "pulse": 0.7,
            "blink_fast": 0.3,
            "strobe": 0.25,
            "breathe_slow": 3.0,
            "glow": 2.0,
        }
        # How far each animation dims at its trough: a glow stays subtle, a pulse swings.
        # Mirrors the firmware's own table for the host-rendered fallback path.
        ANIMATION_FLOORS = {"pulse": 0.25, "breathe_slow": 0.35, "glow": 0.05}

        # Resting attract animation: a single pixel around the perimeter of the grid.
        IDLE_CHASE_AFTER = 30.0  # seconds of no input before it starts
        IDLE_CHASE_COLOR = 0x102030
        IDLE_CHASE_PERIOD_MS = 2500

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)

            self._step_index = 1  # 0.1mm default
            self._last_jog_direction: dict[str, int] = {}
            self._active_continuous_jog_axis: str | None = None
            self._led_cache: dict[int, int] = {}
            self._text_cache: dict[int, str] = {}
            self._banner_cache: tuple[str, str] = ("", "")

            self._stroke_keys: set[int] = set()
            self._stroke_jogged = False
            self._pending_confirm: frozenset | None = None
            self._pending_deadline = 0.0
            self._flash_label = ""
            self._flash_until = 0.0
            self._probe_laser_on = False
            self._idle_since = time.monotonic()
            self._chase_cache: tuple[int, int] | None = None

            self._targets = self._build_targets()

            try:
                self._max_jog_speed = float(Config.get("carvera", "macropad_max_jog_speed"))
            except Exception:
                self._max_jog_speed = self.DEFAULT_MAX_JOG_SPEED

            try:
                self._brightness = max(0, min(100, int(Config.get("carvera", "macropad_brightness"))))
            except Exception:
                self._brightness = 30

            self._daemon = macropad.Daemon(self.executor)
            self._daemon.on_connect = self._handle_connect
            self._daemon.on_disconnect = self._handle_disconnect
            self._daemon.on_update = self._handle_display_update
            self._daemon.on_key_press = self._handle_key_press
            self._daemon.on_key_release = self._handle_key_release
            self._daemon.on_encoder_delta = self._handle_encoder_delta
            self._daemon.on_encoder_press = self._handle_encoder_press
            self._daemon.on_permission_error = self._handle_permission_error

            self._daemon.start()

        def _action_specs(self) -> dict[str, tuple[str, str, Callable[[], None], bool]]:
            """
            Bindable actions by stable name: (banner label, hint label, handler, confirms).

            These names are the vocabulary of macropad_layout.json. Banner labels stay <=8
            characters so they render at a readable size; hint labels are the compact
            legend form. Whether an action confirms is a property of the action itself, not
            of where it is bound, so moving a binding cannot accidentally drop the
            confirmation -- a layout entry can still override it explicitly.
            """
            specs = {
                "margin": ("MARGIN", "MARGIN", self._do_margin_scan, False),
                "machine_home": ("M-HOME", "MHOME", self._do_machine_home, False),
                "safe_z": ("SAFE Z", "SAFEZ", self._do_safe_z, False),
                "work_home": ("W-HOME", "WHOME", self._do_work_home, False),
                "run_pause": ("RUN", "RUN", self._do_start_pause, False),
                "stop": ("STOP", "STOP", self._do_stop, False),
                "spindle_toggle": ("SPINDLE", "SPIN", self._do_spindle_toggle, False),
                "probe_z": ("PROBE Z", "PROBE", self._do_probe_z, False),
                "probe_laser": ("LASER", "LASER", self._do_probe_laser_toggle, False),
                # Zeroing silently redefines the work origin, so it confirms by default.
                "zero_xy": ("ZERO XY", "ZEROXY", self._do_zero_xy, True),
                "zero_z": ("ZERO Z", "ZEROZ", self._do_zero_z, True),
            }
            for n in range(1, 11):
                specs[f"macro_{n}"] = (
                    f"MACRO {n}",
                    f"MAC{n}",
                    (lambda macro_id=n: self.run_macro(macro_id)),
                    False,
                )
            return specs

        def _layout_search_paths(self) -> list[str]:
            """
            Where a layout may live, most specific first: a user copy in the app's config
            directory, then the version shipped alongside this module.
            """
            paths = []
            if App is not None:
                try:
                    app = App.get_running_app()
                    if app is not None and app.user_data_dir:
                        paths.append(os.path.join(app.user_data_dir, self.LAYOUT_FILENAME))
                except Exception:
                    pass
            paths.append(os.path.join(os.path.dirname(__file__), self.LAYOUT_FILENAME))
            return paths

        def _load_layout(self) -> dict:
            """
            First readable, valid layout from _layout_search_paths.

            A layout is taken whole or not at all: a typo'd action name invalidates the
            file rather than silently unbinding one key, because a pendant that is missing
            a key you expect is worse than one that plainly fell back to the default.
            """
            for path in self._layout_search_paths():
                if not os.path.exists(path):
                    continue
                try:
                    with open(path, encoding="utf-8") as handle:
                        raw = json.load(handle)
                    self._validate_layout(raw)
                    return raw
                except Exception as e:
                    logger.error("Ignoring MacroPad layout %s: %s", path, e)
            logger.error("No usable MacroPad layout found; pendant will have no bindings")
            return {"jog": {}, "modifiers": {}}

        def _validate_layout(self, raw: dict) -> None:
            """Raise ValueError describing the first problem found."""
            known = self._action_specs()

            def key_number(value, what):
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    raise ValueError(f"{what} '{value}' is not a key number") from None
                if not 0 <= number < 12:
                    raise ValueError(f"{what} {number} is out of range (keys are 0-11)")
                return number

            if not isinstance(raw, dict):
                raise ValueError("layout must be a JSON object")

            jog = raw.get("jog", {})
            if not isinstance(jog, dict):
                raise ValueError("'jog' must be an object of key -> axis")
            for key, axis in jog.items():
                key_number(key, "jog key")
                if axis not in ("X", "Y", "Z", "A"):
                    raise ValueError(f"jog key {key} has unknown axis '{axis}'")

            modifiers = raw.get("modifiers", {})
            if not isinstance(modifiers, dict):
                raise ValueError("'modifiers' must be an object of key -> modifier")
            seen_names = set()
            for key, entry in modifiers.items():
                number = key_number(key, "modifier key")
                if not isinstance(entry, dict):
                    raise ValueError(f"modifier {number} must be an object")
                name = entry.get("name")
                if not name or not isinstance(name, str):
                    raise ValueError(f"modifier {number} needs a 'name'")
                if name in seen_names:
                    raise ValueError(f"two modifiers are both named '{name}'")
                seen_names.add(name)
                if number in {key_number(k, "jog key") for k in jog}:
                    raise ValueError(f"key {number} is both a jog key and the '{name}' modifier")
                targets = entry.get("targets", {})
                if not isinstance(targets, dict):
                    raise ValueError(f"modifier '{name}' needs 'targets' as an object")
                for target_key, binding in targets.items():
                    target = key_number(target_key, f"'{name}' target key")
                    action = binding.get("action") if isinstance(binding, dict) else binding
                    if action not in known:
                        raise ValueError(
                            f"'{name}' key {target} uses unknown action '{action}'; "
                            f"known actions: {', '.join(sorted(known))}"
                        )

        def _apply_layout(self, raw: dict) -> None:
            """Turn a validated layout into the lookup tables the rest of the class uses."""
            specs = self._action_specs()

            self._jog_keys = {int(key): axis for key, axis in raw.get("jog", {}).items()}

            self._modifier_names = {}
            self._targets = {}
            for key, entry in raw.get("modifiers", {}).items():
                number = int(key)
                self._modifier_names[number] = entry["name"]
                bound = {}
                for target_key, binding in entry.get("targets", {}).items():
                    action = binding.get("action") if isinstance(binding, dict) else binding
                    label, hint, handler, confirms = specs[action]
                    if isinstance(binding, dict) and "confirm" in binding:
                        confirms = bool(binding["confirm"])
                    bound[int(target_key)] = _PendantTarget(label, hint, handler, confirms, action)
                self._targets[number] = bound

            self._modifier_keys = tuple(self._modifier_names)

            # Flatten the tree into the chord dictionary the engine resolves against. The
            # tree still drives the OLED legend and which keys light up; chords are how a
            # stroke is looked up, which is what makes press order irrelevant.
            self._chords = {}
            for modifier, targets in self._targets.items():
                for target_key, target in targets.items():
                    self._chords[frozenset((modifier, target_key))] = target

        def _disabled_reason(self, target: _PendantTarget) -> str | None:
            """
            Why *target* can't run right now, or None if it can.

            Surfaced before the action rather than after: with switches this light you want
            to know a key is unavailable while you can still change your mind, not discover
            it from a firmware alarm afterwards.
            """
            requires = self.ACTION_REQUIRES.get(target.action_name)
            if requires is None:
                return None
            state = str(self._cnc.vars.get("state", ""))
            if state in requires:
                return None
            return self.STATE_HINTS.get(target.action_name, "UNAVAILABLE")

        def _build_targets(self) -> dict[int, dict[int, _PendantTarget]]:
            """Load and apply the layout; returns the target table for convenience."""
            self._apply_layout(self._load_layout())
            return self._targets

        def close(self) -> None:
            self._daemon.stop()

        @property
        def current_step_size(self) -> float:
            return self.STEP_SIZES[self._step_index]

        # --- Connection lifecycle -----------------------------------------------------

        def _handle_connect(self, daemon: macropad.Daemon) -> None:
            # The firmware doesn't persist LED/text state across a reconnect, so drop our
            # "what did we last send" cache and let the next update push everything fresh.
            self._led_cache.clear()
            self._text_cache.clear()
            self._banner_cache = ("", "")
            daemon.set_brightness(self._brightness)
            self._report_connection()

        def _handle_disconnect(self, daemon: macropad.Daemon) -> None:
            self._report_disconnection()

        def _handle_permission_error(self, daemon: macropad.Daemon) -> None:
            message = (
                "The MacroPad pendant was found but cannot be opened due to\n"
                "insufficient permissions on the serial device.\n\n"
                "See the documentation on how to fix these permissions on Linux:\n"
                "https://carvera-community.gitbook.io/docs/controller/features/pendant-support#linux"
            )
            print(f"\nERROR: {message}\n", flush=True)

            scroll = ScrollView(size_hint=(1, 1))
            lbl = Label(text=message, halign="left", valign="top", size_hint_y=None)
            lbl.bind(
                width=lambda w, v: setattr(w, "text_size", (v, None)),
                texture_size=lambda w, v: setattr(w, "height", v[1]),
            )
            scroll.add_widget(lbl)

            btn = Button(text="Exit", size_hint_y=None, height=dp(44))

            content = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
            content.add_widget(scroll)
            content.add_widget(btn)

            popup = Popup(
                title="Pendant Permission Error",
                content=content,
                size_hint=(0.85, 0.65),
                auto_dismiss=False,
            )
            btn.bind(on_release=lambda *_: App.get_running_app().stop())
            popup.open()

        # --- Modifier / target resolution ----------------------------------------------

        # --- Chord engine ----------------------------------------------------------------
        #
        # Input is stenotype-style. A *stroke* starts when the first key goes down from
        # all-idle and accumulates the union of every key pressed during it; it ends when
        # the last key is released, and only then is the accumulated set resolved and run.
        #
        # Accumulating (rather than sampling what is held at any instant) is the whole
        # point: press order, press timing, and which finger lifts first stop mattering, so
        # the pendant cannot do one thing when you meant another because two switches
        # registered microseconds apart. The MacroPad's switches are light and detent-free,
        # which makes that failure easy to provoke and impossible to feel.

        def _chord_for(self, keys: frozenset) -> _PendantTarget | None:
            """The action a set of keys resolves to, or None if it isn't a chord."""
            return self._chords.get(keys)

        def _targets_for(self, modifier: int) -> dict[int, _PendantTarget]:
            return self._targets.get(modifier, {})

        def _stroke_jog_axis(self) -> str | None:
            """
            Axis to jog for the stroke in progress.

            Jogging is a sustained hold rather than a chord, so it only applies while the
            stroke consists of exactly one jog key -- adding any second key means a chord is
            being formed, not a jog.
            """
            if len(self._stroke_keys) != 1:
                return None
            return self._jog_keys.get(next(iter(self._stroke_keys)))

        def _handle_key_press(self, daemon: macropad.Daemon, key_number: int) -> None:
            self._stroke_keys.add(key_number)

        def _handle_key_release(self, daemon: macropad.Daemon, key_number: int) -> None:
            axis = self._jog_keys.get(key_number)
            if axis is not None:
                self._last_jog_direction.pop(axis, None)
                if axis == self._active_continuous_jog_axis and self._controller.continuous_jog_active:
                    self._controller.stopContinuousJog()
                    self._active_continuous_jog_axis = None
                    if self._update_ui_on_jog_stop:
                        self._update_ui_on_jog_stop()

            if daemon.pressed_keys:
                return  # stroke still in progress
            self._end_stroke()

        def _end_stroke(self) -> None:
            """Resolve and run the completed stroke, then reset for the next one."""
            chord = frozenset(self._stroke_keys)
            jogged = self._stroke_jogged
            self._stroke_keys = set()
            self._stroke_jogged = False

            if not chord or jogged:
                # A stroke that jogged is spent: releasing the axis key must not also fire
                # whatever chord those keys happen to spell.
                return

            target = self._chord_for(chord)
            if target is None:
                self._flash("NO CHORD")
                return

            reason = self._disabled_reason(target)
            if reason is not None:
                self._flash(reason)
                return

            if target.needs_confirm and self._pending_confirm != chord:
                self._pending_confirm = chord
                self._pending_deadline = time.monotonic() + self.CONFIRM_TIMEOUT
                return

            self._clear_pending_confirm()
            self._flash(target.label)
            target.action()

        def _flash(self, label: str) -> None:
            self._flash_label = label
            self._flash_until = time.monotonic() + self.FLASH_DURATION

        def _clear_pending_confirm(self) -> None:
            self._pending_confirm = None
            self._pending_deadline = 0.0

        def _expire_pending_confirm(self) -> None:
            """Drop a stale confirmation so a forgotten prompt can't fire much later."""
            if self._pending_confirm and time.monotonic() > self._pending_deadline:
                self._clear_pending_confirm()

        def _handle_encoder_delta(self, daemon: macropad.Daemon, delta: int) -> None:
            if not self._is_jogging_enabled():
                return

            axis = self._stroke_jog_axis()
            if axis is None:
                return

            self._stroke_jogged = True

            if self._controller.jog_mode == Controller.JOG_MODE_CONTINUOUS:
                self._handle_continuous_jog(axis, delta)
            else:
                distance = self.current_step_size * delta
                self._controller.jog(f"{axis}{round(distance, 4)}")

        def _handle_continuous_jog(self, axis: str, delta: int) -> None:
            direction = 1 if delta > 0 else -1
            prev_direction = self._last_jog_direction.get(axis, 0)

            if (
                prev_direction != 0
                and prev_direction != direction
                and axis == self._active_continuous_jog_axis
                and self._controller.continuous_jog_active
            ):
                self._controller.stopContinuousJog()

            self._last_jog_direction[axis] = direction

            if self._controller.continuous_jog_active:
                return

            feed = self.STEP_SIZE_SPEED_FRACTION[self.current_step_size] * self._max_jog_speed
            if axis == "Z":
                feed = min(feed, self.Z_MAX_JOG_SPEED)

            self._controller.startContinuousJog(f"{axis}{direction}", feed)
            self._active_continuous_jog_axis = axis

        def _handle_encoder_press(self, daemon: macropad.Daemon) -> None:
            # Doubles as an explicit "never mind" for a pending confirmation.
            if self._pending_confirm:
                self._clear_pending_confirm()
                return

            self._step_index = (self._step_index + 1) % len(self.STEP_SIZES)
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("step_size_changed")

        # --- Action implementations (mirrors GamepadPendant's) --------------------------

        def _do_start_pause(self) -> None:
            self._handle_run_pause_resume()
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("start_pause")

        def _do_stop(self) -> None:
            self._controller.abortCommand()
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("stop")

        def _do_zero_xy(self) -> None:
            # G10L20P0: make the current machine position the given work coordinate.
            self._controller.wcs_set(x=0, y=0)
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("zero_xy")

        def _do_zero_z(self) -> None:
            self._controller.wcs_set(z=0)
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("zero_z")

        def _do_probe_z(self) -> None:
            self._handle_probe_z()
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("probe_z")

        def _do_machine_home(self) -> None:
            self._controller.gotoMachineHome()
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("m_home")

        def _do_safe_z(self) -> None:
            self._controller.gotoSafeZ()
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("safe_z")

        def _do_work_home(self) -> None:
            self._controller.gotoWCSHome()
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("w_home")

        def _is_spindle_running(self) -> bool:
            try:
                return float(self._cnc.vars.get("curspindle", 0)) > 0.0
            except (TypeError, ValueError):
                return False

        def _do_spindle_toggle(self) -> None:
            if self._cnc.vars.get("lasermode"):
                return
            self._controller.setSpindleSwitch(not self._is_spindle_running())
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("spindle_on_off")

        def _margin_bounds_ready(self) -> bool:
            """
            Whether the loaded file's extents are usable for a margin scan.

            With no file loaded CNC.vars holds sentinels (xmin/ymin = +1e6,
            xmax/ymax = -1e6), and Controller.autoCommand silently returns for out-of-range
            bounds. Silent nothing is a bad pendant response, so the same conditions are
            checked here in order to say why on the OLED instead.
            """
            v = self._cnc.vars
            try:
                xmin, xmax = float(v["xmin"]), float(v["xmax"])
                ymin, ymax = float(v["ymin"]), float(v["ymax"])
                worksize_x, worksize_y = float(v["worksize_x"]), float(v["worksize_y"])
            except (KeyError, TypeError, ValueError):
                return False
            if xmax <= xmin or ymax <= ymin:  # sentinels, or a file with no XY motion
                return False
            # Mirrors autoCommand's own reject, so we never send what it would drop.
            return abs(xmin) <= worksize_x and abs(ymin) <= worksize_y

        def _do_margin_scan(self) -> None:
            """
            Trace the loaded file's XY bounding box (M495 X.. Y.. C.. D..).

            Re-runnable at any time, unlike the app's run-time-only margin checkbox.
            """
            if self._cnc.vars.get("lasermode"):
                # Firmware answers "Can not do Automatic work in laser mode" with an ALARM;
                # say so on the pendant rather than provoking it.
                self._flash_label = "LASER MODE"
                return
            if not self._margin_bounds_ready():
                self._flash_label = "NO FILE"
                return

            self._controller.autoCommand(margin=True)
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("margin")

        def _do_probe_laser_toggle(self) -> None:
            """
            Toggle the wired probe's laser crosshair (M494) -- not laser mode.

            The machine reports no probe-laser state, so this tracks it locally. The
            firmware also drops the laser on its own after ~5 minutes and when a tool is
            loaded, so the local flag can drift out of sync; the banner names the command
            actually sent, and both directions are idempotent, so a stale flag costs at
            worst one extra press.
            """
            self._probe_laser_on = not self._probe_laser_on
            self._controller.setProbeLaser(self._probe_laser_on)
            # Overrides the generic label flash set before the action ran.
            self._flash_label = "LASER ON" if self._probe_laser_on else "LASER OFF"
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("probe_laser")

        # --- Display / LED feedback -----------------------------------------------------

        @staticmethod
        def _safe_number(value, lo: float, hi: float) -> float:
            try:
                num = float(value)
            except OverflowError:
                # A finite integer literal can exceed float's range; clamp it by sign.
                return hi if value > 0 else lo
            except (TypeError, ValueError):
                return 0.0
            if not math.isfinite(num):
                return 0.0
            return max(lo, min(num, hi))

        def _modifier_color(self, modifier: int) -> int:
            name = self._modifier_names.get(modifier)
            return self.MODIFIER_COLORS.get(name, self.DEFAULT_MODIFIER_COLOR)

        def _target_color(self, modifier: int) -> int:
            name = self._modifier_names.get(modifier)
            return self.TARGET_COLORS.get(name, self.DEFAULT_TARGET_COLOR)

        def _set_led_if_changed(self, daemon: macropad.Daemon, key: int, color: int) -> None:
            if self._led_cache.get(key) != color:
                daemon.set_led(key, color)
                self._led_cache[key] = color

        def _set_text_if_changed(self, daemon: macropad.Daemon, row: int, text: str) -> None:
            if self._text_cache.get(row) != text:
                daemon.set_text(row, text)
                self._text_cache[row] = text

        def _set_banner_if_changed(self, daemon: macropad.Daemon, banner: str, hint: str) -> None:
            if self._banner_cache == (banner, hint):
                return
            self._banner_cache = (banner, hint)

            if not daemon.supports_banner:
                # Firmware 1 has no BANNER/HINT; fall back to the small row grid so an
                # un-updated pendant still shows what's about to happen.
                self._set_text_if_changed(daemon, 0, banner)
                self._set_text_if_changed(daemon, 1, hint.replace("|", " "))
                return

            daemon.set_banner(banner)
            daemon.set_hint(hint)

        def _handle_display_update(self, daemon: macropad.Daemon) -> None:
            self._expire_pending_confirm()
            self._refresh_display(daemon)
            self._refresh_leds(daemon)

        def _hint_for(self, modifier: int) -> str:
            """
            Legend of available targets, split into two lines mirroring the physical key
            rows they sit on -- so the on-screen order matches the keys under your fingers.
            """
            by_row: dict[int, list[str]] = {}
            for key, target in sorted(self._targets_for(modifier).items()):
                labels = by_row.setdefault(key // 3, [])
                if target.hint not in labels:  # e.g. ZERO XY is bound to two keys
                    labels.append(target.hint)
            return "|".join(" ".join(by_row[row]) for row in sorted(by_row))[: self.MAX_HINT_LEN]

        def _display_context(self) -> tuple[str, str] | None:
            """
            (banner, hint) for the current input state, or None when nothing is engaged and
            the DRO should be shown instead.
            """
            if self._cnc.vars.get("state") == "Alarm":
                return ("ALARM", "UNLOCK IN APP")

            if self._pending_confirm:
                target = self._chord_for(self._pending_confirm)
                if target is not None:
                    return (f"{target.label}?", "REPEAT CHORD TO CONFIRM")

            # Mid-stroke: show what releasing right now would do, so the display leads the
            # action instead of reporting it afterwards.
            if self._stroke_keys:
                chord = frozenset(self._stroke_keys)
                target = self._chord_for(chord)
                if target is not None:
                    reason = self._disabled_reason(target)
                    if reason is not None:
                        return (target.label, reason)
                    return (target.label, "RELEASE TO RUN")

                if len(chord) == 1:
                    only = next(iter(chord))
                    if only in self._modifier_names:
                        return (self._modifier_names[only], self._hint_for(only))

            axis = self._stroke_jog_axis()
            if axis is not None:
                # Live work coordinate of the axis being jogged. The value is right-aligned
                # in a fixed-width field so the banner's auto-scaling picks one size and
                # keeps it -- otherwise the text would visibly jump between sizes as digits
                # come and go while jogging. Freshness is bounded by the host's status poll
                # rate, not by this refresh.
                pos = self._safe_number(self._cnc.vars.get(f"w{axis.lower()}", 0), -1e6, 1e6)
                return (
                    f"{axis}{pos:>{self.JOG_READOUT_WIDTH}.3f}",
                    f"JOG {axis}  STEP {self.current_step_size:g}mm",
                )

            if self._stroke_keys:
                # Keys are down but spell nothing. Say so while it can still be corrected
                # by adding a key, rather than staying blank until release.
                return ("- - -", "NO CHORD")

            if self._flash_label and time.monotonic() < self._flash_until:
                return (self._flash_label, "")

            return None

        def _refresh_display(self, daemon: macropad.Daemon) -> None:
            if daemon.num_rows == 0:
                return  # haven't completed the ID handshake yet

            context = self._display_context()
            if context is not None:
                self._set_banner_if_changed(daemon, context[0], context[1])
                return

            # Nothing engaged -> back to grid mode with the DRO.
            if self._banner_cache != ("", ""):
                self._banner_cache = ("", "")
                self._text_cache.clear()
                if daemon.supports_banner:
                    daemon.set_banner("")

            self._refresh_dro(daemon)

        def _refresh_dro(self, daemon: macropad.Daemon) -> None:
            def wpos(key: str) -> str:
                return f"{self._safe_number(self._cnc.vars.get(key, 0), -1e6, 1e6):.3f}"

            feed = self._safe_number(self._cnc.vars.get("curfeed", 0), 0.0, 9999.0)
            spindle = self._safe_number(self._cnc.vars.get("curspindle", 0), 0.0, 65535.0)
            state = self._cnc.vars.get("state", "")

            lines = [
                f"X {wpos('wx')}",
                f"Y {wpos('wy')}",
                f"Z {wpos('wz')}",
                f"A {wpos('wa')}",
                f"F{feed:.0f} S{spindle:.0f}",
                f"{state} step={self.current_step_size:g}mm",
            ]

            cols = daemon.num_cols or None
            for row, text in enumerate(lines[: daemon.num_rows]):
                self._set_text_if_changed(daemon, row, text[:cols] if cols else text)

        def _completions(self, chord: frozenset) -> set[int]:
            """Keys that, added to the current stroke, would complete some chord."""
            candidates = set()
            for known in self._chords:
                if chord < known:
                    candidates |= known - chord
            return candidates

        def _led_plan(self) -> dict[int, tuple[int, str]]:
            """
            Per-key (colour, animation) for the current state.

            Split out from sending so the animation vocabulary is decided in one place, and
            so it can be handed to the firmware to render once the protocol carries
            animations -- the caller does not care which end draws them.

            The stroke preview is the important part. These switches have no detent, so the
            only way to know the pad registered the key you brushed -- or one you didn't --
            is to see it. Every key counted in the stroke pulses together, in one colour
            that says whether the set means anything yet.
            """
            plan = dict.fromkeys(range(12), (0x000000, "solid"))

            if self._cnc.vars.get("state") == "Alarm":
                return dict.fromkeys(range(12), (0xFF0000, "strobe"))

            if self._pending_confirm:
                target = self._chord_for(self._pending_confirm)
                for key in self._pending_confirm:
                    plan[key] = (self.CONFIRM_COLOR, "blink_fast")
                if target is not None:
                    return plan

            if self._stroke_keys:
                chord = frozenset(self._stroke_keys)
                target = self._chord_for(chord)

                if target is None:
                    colour, anim = self.CHORD_FORMING_COLOR, "pulse"
                elif self._disabled_reason(target) is not None:
                    colour, anim = self.CHORD_BLOCKED_COLOR, "blink_fast"
                else:
                    # Resolved and allowed: stop moving. Going steady is the closest thing
                    # to a detent this pad can give you -- let go now and it runs.
                    colour, anim = self.CHORD_READY_COLOR, "solid"

                for key in chord:
                    plan[key] = (colour, anim)
                for key in self._completions(chord):
                    plan[key] = (self._completion_color(key), "glow")
                return plan

            # Idle: where the modifiers are, and the jog axes.
            for key, axis in self._jog_keys.items():
                plan[key] = (self.AXIS_COLOR_IDLE[axis], "solid")
            for key in self._modifier_keys:
                plan[key] = (self._modifier_color(key), "breathe_slow")
            return plan

        def _completion_color(self, key: int) -> int:
            """Colour for a key offered as a completion: its own modifier's, if it is one."""
            if key in self._modifier_names:
                return self._modifier_color(key)
            for modifier, targets in self._targets.items():
                if key in targets:
                    return self._target_color(modifier)
            return self.DEFAULT_TARGET_COLOR

        def _idle_chase(self) -> tuple[int, int] | None:
            """
            (colour, period_ms) for the resting attract animation, or None if not resting.

            Only after a spell of real inactivity, so it never competes with feedback you
            are actually trying to read.
            """
            if self._cnc.vars.get("state") == "Alarm" or self._stroke_keys or self._pending_confirm:
                return None
            if time.monotonic() < self._idle_since + self.IDLE_CHASE_AFTER:
                return None
            return (self.IDLE_CHASE_COLOR, self.IDLE_CHASE_PERIOD_MS)

        def _refresh_leds(self, daemon: macropad.Daemon) -> None:
            if self._stroke_keys or self._pending_confirm:
                self._idle_since = time.monotonic()

            chase = self._idle_chase() if daemon.supports_animation else None
            if chase != self._chase_cache:
                self._chase_cache = chase
                self._led_cache.clear()  # the device owns every pixel while chasing
                if chase is None:
                    daemon.clear_chase()
                else:
                    daemon.set_chase(*chase)
            if chase is not None:
                return

            for key, (color, animation) in self._led_plan().items():
                if daemon.supports_animation:
                    self._set_led_anim_if_changed(daemon, key, color, animation)
                else:
                    # Firmware 1-2 can only be told a colour, so step the animation here.
                    self._set_led_if_changed(daemon, key, self._render(color, animation))

        def _set_led_anim_if_changed(self, daemon: macropad.Daemon, key: int, color: int, animation: str) -> None:
            if self._led_cache.get(key) == (color, animation):
                return
            self._led_cache[key] = (color, animation)
            if animation == "solid":
                daemon.set_led(key, color)
            else:
                daemon.set_led_anim(
                    key,
                    self.FIRMWARE_ANIMATION_NAMES.get(animation, animation),
                    color,
                    int(self.ANIMATION_PERIODS[animation] * 1000),
                )

        def _render(self, color: int, animation: str) -> int:
            """
            Collapse (colour, animation) to the colour to show this instant.

            Animation is driven from here for now, which costs one LED write per key per
            visible change. Moving it into the firmware is the obvious next step -- the
            board can render a smooth pulse locally with no serial traffic at all -- and
            this is the only function that has to change when it does.
            """
            if animation == "solid":
                return color
            phase = (time.monotonic() % self.ANIMATION_PERIODS[animation]) / self.ANIMATION_PERIODS[animation]
            if animation in ("blink_fast", "strobe"):
                return color if phase < 0.5 else 0x000000
            # pulse / breathe_slow / glow: triangle ramp between a floor and the full colour.
            floor = self.ANIMATION_FLOORS[animation]
            level = floor + (1.0 - floor) * (2 * phase if phase < 0.5 else 2 * (1 - phase))
            return self._scale_color(color, level)

        @staticmethod
        def _scale_color(color: int, level: float) -> int:
            level = max(0.0, min(1.0, level))
            r = int(((color >> 16) & 0xFF) * level)
            g = int(((color >> 8) & 0xFF) * level)
            b = int((color & 0xFF) * level)
            return (r << 16) | (g << 8) | b


class GamepadPendant(Pendant):
    DEFAULT_MAX_JOG_SPEED = 3000  # mm/min
    DEFAULT_Z_MAX_SPEED = 800  # mm/min cap for Z in continuous mode
    STEP_SIZES = [0.01, 0.1, 1.0, 10.0]  # step sizes in mm
    STEP_SIZE_SPEED_FRACTION = {  # speed fractions for continuous jog
        0.01: 0.02,
        0.1: 0.10,
        1.0: 0.30,
        10.0: 1.0,
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._last_jog_direction = {}  # action -> direction sign
        self._active_continuous_jog_action: str | None = None

        deadzone = 0.15
        try:
            deadzone = float(Config.get("carvera", "gamepad_deadzone"))
        except Exception:
            pass

        self._step_index = 1  # Start at 0.1mm

        try:
            self._max_jog_speed = float(Config.get("carvera", "gamepad_max_jog_speed"))
        except Exception:
            self._max_jog_speed = self.DEFAULT_MAX_JOG_SPEED

        self._invert_x = Config.getboolean("carvera", "gamepad_invert_x", fallback=False)
        self._invert_y = Config.getboolean("carvera", "gamepad_invert_y", fallback=False)
        self._invert_z = Config.getboolean("carvera", "gamepad_invert_z", fallback=False)
        self._invert_a = Config.getboolean("carvera", "gamepad_invert_a", fallback=False)

        bindings = gamepad_module.GamepadBindings.from_config()
        self._manager = gamepad_module.GamepadManager(bindings=bindings, deadzone=deadzone)
        self._manager.on_connect = self._handle_connect
        self._manager.on_jog_axis = self._handle_jog_axis
        self._manager.on_jog_stop = self._handle_jog_stop
        self._manager.on_button_action = self._handle_button_action

    def close(self) -> None:
        if self._controller.stream is not None:
            self._controller.executeRealtime(0x19)
        self._controller._clear_continuous_jog_state()
        self._active_continuous_jog_action = None
        self._manager.close()
        self._report_disconnection()

    @property
    def current_step_size(self) -> float:
        return self.STEP_SIZES[self._step_index]

    def _cycle_step_size(self, direction: int) -> None:
        n = len(self.STEP_SIZES)
        new_index = max(0, min(n - 1, self._step_index + direction))
        if new_index == self._step_index:
            return
        self._step_index = new_index
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("step_size_changed")

    def _continuous_feed_for_axis(self, axis: str) -> float:
        frac = self.STEP_SIZE_SPEED_FRACTION[self.current_step_size]
        cap = self._max_jog_speed
        if axis == "Z":
            cap = min(cap, self.DEFAULT_Z_MAX_SPEED)
        return frac * cap

    # Callbacks
    def _handle_connect(self) -> None:
        self._report_connection()

    def _handle_jog_axis(self, action: str, value: float) -> None:
        if not self._is_jogging_enabled():
            return

        axis_letter = self._action_to_axis(action)
        if axis_letter is None:
            return

        if value == 0.0:
            return

        value = self._apply_inversion(axis_letter, value)
        direction = 1 if value > 0 else -1

        if self._controller.jog_mode == Controller.JOG_MODE_STEP:
            self._handle_step_jog(action, axis_letter, direction)
        else:
            self._handle_continuous_jog(action, axis_letter, value, direction)

    def _handle_step_jog(self, action: str, axis: str, direction: int) -> None:
        if self._manager.is_axis_held(action) and self._last_jog_direction.get(action) == direction:
            return

        self._last_jog_direction[action] = direction
        distance = self.current_step_size * direction
        self._controller.jog(f"{axis}{round(distance, 4)}")

    def _handle_continuous_jog(self, action: str, axis: str, value: float, direction: int) -> None:
        prev_direction = self._last_jog_direction.get(action, 0)
        self._last_jog_direction[action] = direction

        if (
            prev_direction != 0
            and prev_direction != direction
            and self._controller.continuous_jog_active
            and action == self._active_continuous_jog_action
        ):
            self._controller.stopContinuousJog()

        if self._controller.continuous_jog_active:
            return

        feed = self._continuous_feed_for_axis(axis)

        if self._controller.jog_mode != Controller.JOG_MODE_CONTINUOUS:
            self._controller.setJogMode(Controller.JOG_MODE_CONTINUOUS)

        self._controller.startContinuousJog(f"{axis}{direction}", feed)
        self._active_continuous_jog_action = action

    def _handle_jog_stop(self, action: str) -> None:
        self._last_jog_direction.pop(action, None)

        if action != self._active_continuous_jog_action:
            return

        self._active_continuous_jog_action = None
        if self._controller.continuous_jog_active:
            self._controller.stopContinuousJog()
            if self._update_ui_on_jog_stop:
                self._update_ui_on_jog_stop()

    def _handle_button_action(self, action: str) -> None:
        action_map = {
            "reset": self._do_reset,
            "stop": self._do_stop,
            "start_pause": self._do_start_pause,
            "mode_toggle": self._do_mode_toggle,
            "feed_plus": self._do_feed_plus,
            "feed_minus": self._do_feed_minus,
            "spindle_plus": self._do_spindle_plus,
            "spindle_minus": self._do_spindle_minus,
            "m_home": self._do_machine_home,
            "safe_z": self._do_safe_z,
            "w_home": self._do_work_home,
            "spindle_on_off": self._do_spindle_toggle,
            "probe_z": self._do_probe_z,
            "step_size_up": lambda: self._cycle_step_size(1),
            "step_size_down": lambda: self._cycle_step_size(-1),
        }

        # Macro buttons (macro_1 .. macro_10)
        if action.startswith("macro_"):
            try:
                macro_id = int(action.split("_", 1)[1])
                self.run_macro(macro_id)
            except (ValueError, IndexError):
                pass
            return

        handler = action_map.get(action)
        if handler:
            handler()

    # Action implementations

    def _do_reset(self) -> None:
        self._controller.estopCommand()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("reset")

    def _do_stop(self) -> None:
        self._controller.abortCommand()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("stop")

    def _do_start_pause(self) -> None:
        self._handle_run_pause_resume()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("start_pause")

    def _do_mode_toggle(self) -> None:
        if self._controller.jog_mode == Controller.JOG_MODE_STEP:
            if not self._controller.is_community_firmware:
                return
            self._controller.setJogMode(Controller.JOG_MODE_CONTINUOUS)
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("mode_continuous")
        else:
            self._controller.setJogMode(Controller.JOG_MODE_STEP)
            if self._update_ui_on_button_press:
                self._update_ui_on_button_press("mode_step")

    def _do_feed_plus(self) -> None:
        self._feed_override.on_increase()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("feed_plus")

    def _do_feed_minus(self) -> None:
        self._feed_override.on_decrease()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("feed_minus")

    def _do_spindle_plus(self) -> None:
        self._spindle_override.on_increase()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("spindle_plus")

    def _do_spindle_minus(self) -> None:
        self._spindle_override.on_decrease()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("spindle_minus")

    def _do_machine_home(self) -> None:
        self._controller.gotoMachineHome()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("m_home")

    def _do_safe_z(self) -> None:
        self._controller.gotoSafeZ()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("safe_z")

    def _do_work_home(self) -> None:
        self._controller.gotoWCSHome()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("w_home")

    def _do_spindle_toggle(self) -> None:
        if self._cnc.vars.get("lasermode"):
            return
        running = float(self._cnc.vars.get("curspindle", 0)) > 0.0
        self._controller.setSpindleSwitch(not running)
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("spindle_on_off")

    def _do_probe_z(self) -> None:
        self._handle_probe_z()
        if self._update_ui_on_button_press:
            self._update_ui_on_button_press("probe_z")

    # Helpers

    @staticmethod
    def _action_to_axis(action: str) -> str | None:
        mapping = {
            "jog_x": "X",
            "jog_y": "Y",
            "jog_z": "Z",
            "jog_a": "A",
        }
        return mapping.get(action)

    def _apply_inversion(self, axis: str, value: float) -> float:
        if axis == "X" and self._invert_x:
            return -value
        if axis == "Y" and self._invert_y:
            return -value
        if axis == "Z" and self._invert_z:
            return -value
        if axis == "A" and self._invert_a:
            return -value
        return value


SUPPORTED_PENDANTS = {
    "None": NonePendant,
    "Gamepad": GamepadPendant,
}

if WHB04_SUPPORTED:
    SUPPORTED_PENDANTS["WHB04"] = WHB04

if MACROPAD_SUPPORTED:
    SUPPORTED_PENDANTS["MacroPad"] = MacroPadPendant


class SettingPendantSelector(SettingItem):
    # Populated by load_pendant_config from pendant_config.json.
    # Maps setting key -> list of pendant type names that should show it.
    # Keys absent from this map are always visible.
    pendant_types_map = {}

    def __init__(self, **kwargs):
        wrapper = AnchorLayout(anchor_y="center", anchor_x="left")

        self.spinner = Spinner(text="None", values=list(SUPPORTED_PENDANTS.keys()), size_hint=(1, None), height="36dp")
        super().__init__(**kwargs)
        self.spinner.bind(text=self.on_spinner_select)
        wrapper.add_widget(self.spinner)
        self.add_widget(wrapper)
        self._widget_order = None

    def on_spinner_select(self, spinner, text):
        self.value = text
        self.panel.set_value(self.section, self.key, text)
        self._update_sibling_visibility(text)

    def on_value(self, instance, value):
        if self.spinner.text != value:
            self.spinner.text = value
        Clock.schedule_once(lambda _: self._update_sibling_visibility(value), 0)

    def _update_sibling_visibility(self, pendant_type: str) -> None:
        if not hasattr(self, "panel") or self.panel is None:
            return

        if self._widget_order is None:
            self._widget_order = list(reversed(self.panel.children[:]))

        self.panel.clear_widgets()
        for widget in self._widget_order:
            key = getattr(widget, "key", None)
            if key is not None and widget is not self:
                allowed = self.pendant_types_map.get(key)
                if allowed is not None and pendant_type not in allowed:
                    continue
            self.panel.add_widget(widget)


class GamepadBindingsPopup(Popup):
    """Interactive popup for configuring gamepad button/axis bindings."""

    AXIS_LISTEN_THRESHOLD = 0.6

    def __init__(self, current_bindings: dict, on_save: Callable[[dict], None], manager=None, **kwargs) -> None:
        kwargs.setdefault("title", "Gamepad Bindings")
        kwargs.setdefault("size_hint", (0.9, 0.9))
        kwargs.setdefault("auto_dismiss", False)
        super().__init__(**kwargs)

        self._on_save = on_save
        self._manager = manager  # GamepadManager paused while popup is open
        self._bindings = json.loads(json.dumps(current_bindings))
        self._listening_for = None  # (action, category) when in listen mode
        self._listen_btn = None
        self._listen_label = None
        self._row_widgets = {}  # action -> (label_widget, bind_btn)

        self._build_ui()

    def _build_ui(self) -> None:
        root = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))

        scroll = ScrollView(size_hint=(1, 1))
        self._list_layout = BoxLayout(
            orientation="vertical", size_hint_y=None, spacing=dp(4), padding=[0, 0, dp(12), 0]
        )
        self._list_layout.bind(minimum_height=self._list_layout.setter("height"))

        for group_name, actions in gamepad_module.BINDING_GROUPS:
            header = Label(
                text=f"[b]{tr._(group_name)}[/b]",
                markup=True,
                size_hint_y=None,
                height=dp(32),
                halign="left",
                valign="middle",
            )
            header.bind(size=lambda w, s: setattr(w, "text_size", s))
            self._list_layout.add_widget(header)

            for action in actions:
                self._add_action_row(action)

        scroll.add_widget(self._list_layout)
        root.add_widget(scroll)

        btn_bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))

        self._preset_spinner = Spinner(
            text=tr._("Load preset..."),
            values=gamepad_module.preset_names(),
            size_hint_x=0.45,
        )
        self._preset_spinner.bind(text=self._on_preset_selected)

        cancel_btn = Button(text=tr._("Cancel"))
        cancel_btn.bind(on_release=lambda *_: self._cancel())

        save_btn = Button(text=tr._("Save"))
        save_btn.bind(on_release=lambda *_: self._save())

        btn_bar.add_widget(self._preset_spinner)
        btn_bar.add_widget(cancel_btn)
        btn_bar.add_widget(save_btn)
        root.add_widget(btn_bar)

        self.content = root

    def open(self, *args, **kwargs) -> None:
        super().open(*args, **kwargs)
        if self._manager is not None:
            self._manager.paused = True

    def _add_action_row(self, action: str) -> None:
        label_text = tr._(gamepad_module.ACTION_LABELS.get(action, action))
        current = self._describe_binding(action)

        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(36), spacing=dp(6))

        name_lbl = Label(text=label_text, size_hint_x=0.35, halign="left", valign="middle")
        name_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))

        binding_lbl = Label(text=current, size_hint_x=0.35, halign="center", valign="middle")
        binding_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))

        bind_btn = Button(text=tr._("Bind..."), size_hint_x=0.15)
        bind_btn.bind(on_release=lambda btn, a=action, bl=binding_lbl: self._start_listening(a, btn, bl))

        unbind_btn = Button(text=tr._("Unbind"), size_hint_x=0.15)
        unbind_btn.bind(on_release=lambda *_, a=action: self._unbind_action(a))

        row.add_widget(name_lbl)
        row.add_widget(binding_lbl)
        row.add_widget(bind_btn)
        row.add_widget(unbind_btn)
        self._list_layout.add_widget(row)
        self._row_widgets[action] = (binding_lbl, bind_btn)

    def _describe_binding(self, action: str) -> str:
        for category in ("axes", "triggers", "buttons"):
            mapping = self._bindings.get(category, {})
            for input_id, bound_action in mapping.items():
                if bound_action == action:
                    itype = "axis" if category == "axes" else "trigger" if category == "triggers" else "button"
                    return gamepad_module.format_input_label(itype, input_id)

        hat = self._bindings.get("hat", {})
        hat_dirs = [d for d in ("up", "down", "left", "right") if hat.get(d) == action]
        if hat_dirs:
            return ", ".join(gamepad_module.format_input_label("hat", d) for d in hat_dirs)

        return tr._("Unbound")

    def _refresh_all_binding_labels(self) -> None:
        for act, (lbl, _) in self._row_widgets.items():
            lbl.text = self._describe_binding(act)

    def _start_listening(self, action: str, btn, binding_lbl) -> None:
        if self._listening_for is not None:
            self._stop_listening()

        self._listening_for = action
        self._listen_btn = btn
        self._listen_label = binding_lbl
        btn.text = tr._("Waiting...")
        Window.bind(
            on_joy_axis=self._listen_axis,
            on_joy_button_down=self._listen_button,
            on_joy_hat=self._listen_hat,
        )

    def _stop_listening(self) -> None:
        Window.unbind(
            on_joy_axis=self._listen_axis,
            on_joy_button_down=self._listen_button,
            on_joy_hat=self._listen_hat,
        )
        if self._listen_btn:
            self._listen_btn.text = tr._("Bind...")
        self._listening_for = None
        self._listen_btn = None
        self._listen_label = None

    def _apply_captured(self, category: str, input_id: str) -> None:
        action = self._listening_for
        if action is None:
            return

        # Axes and triggers share on_joy_axis events. Jog axes claim the
        # whole physical axis, while triggers only claim one direction.
        if category == "axes":
            self._bindings.get("axes", {}).pop(input_id, None)
            trigger_map = self._bindings.get("triggers", {})
            for key in list(trigger_map.keys()):
                trigger_axis_id, _ = gamepad_module.split_trigger_id(key)
                if trigger_axis_id == input_id:
                    del trigger_map[key]
            self._remove_action_binding(action)
            self._bindings.setdefault("axes", {})[input_id] = action
        elif category == "triggers":
            trigger_axis_id, trigger_direction = gamepad_module.split_trigger_id(input_id)
            self._bindings.get("axes", {}).pop(trigger_axis_id, None)
            trigger_map = self._bindings.get("triggers", {})
            trigger_map.pop(input_id, None)
            trigger_map.pop(trigger_axis_id, None)
            self._remove_action_binding(action)
            self._bindings.setdefault("triggers", {})[input_id] = action
        elif category == "buttons":
            self._bindings.get("buttons", {}).pop(input_id, None)
            self._remove_action_binding(action)
            self._bindings.setdefault("buttons", {})[input_id] = action
        elif category == "hat":
            pair = gamepad_module.hat_axis_pair(input_id) if action.startswith("jog_") else None
            if pair:
                # Jog on D-Pad: bind both directions on the same hat axis.
                self._remove_action_binding(action)
                hat_map = self._bindings.setdefault("hat", {})
                for d in pair:
                    hat_map.pop(d, None)
                for d in pair:
                    hat_map[d] = action
            else:
                self._bindings.get("hat", {}).pop(input_id, None)
                self._remove_action_binding(action)
                self._bindings.setdefault("hat", {})[input_id] = action
        else:
            return

        self._stop_listening()
        self._refresh_all_binding_labels()

    def _remove_action_binding(self, action: str) -> None:
        for cat in ("axes", "triggers", "buttons"):
            cat_map = self._bindings.get(cat, {})
            to_del = [k for k, v in cat_map.items() if v == action]
            for k in to_del:
                del cat_map[k]
        hat_map = self._bindings.get("hat", {})
        to_del = [k for k, v in hat_map.items() if v == action]
        for k in to_del:
            del hat_map[k]

    def _unbind_action(self, action: str) -> None:
        if self._listening_for is not None:
            self._stop_listening()
        self._remove_action_binding(action)
        self._refresh_all_binding_labels()

    def _listen_axis(self, win, stickid, axisid, value) -> None:
        normalised = max(-1.0, min(1.0, value / gamepad_module.AXIS_MAX))
        if abs(normalised) < self.AXIS_LISTEN_THRESHOLD:
            return
        action = self._listening_for
        if action is None:
            return
        # Only jog actions use continuous axis semantics; everything else
        # should be stored as a trigger (one-shot on threshold crossing).
        is_axis = action in ("jog_x", "jog_y", "jog_z", "jog_a")
        if is_axis:
            self._apply_captured("axes", str(axisid))
        else:
            direction = "+" if normalised > 0 else "-"
            trigger_id = gamepad_module.trigger_input_id(axisid, direction)
            self._apply_captured("triggers", trigger_id)

    def _listen_button(self, win, stickid, buttonid) -> None:
        self._apply_captured("buttons", str(buttonid))

    def _listen_hat(self, win, stickid, hatid, value) -> None:
        dx, dy = value if isinstance(value, (tuple, list)) else (0, 0)
        direction = None
        if dy > 0:
            direction = "up"
        elif dy < 0:
            direction = "down"
        elif dx < 0:
            direction = "left"
        elif dx > 0:
            direction = "right"
        if direction:
            self._apply_captured("hat", direction)

    def _on_preset_selected(self, spinner, name: str) -> None:
        if name not in gamepad_module.preset_names():
            return
        bindings = gamepad_module.preset_bindings(name)
        if bindings is None:
            return
        if self._listening_for is not None:
            self._stop_listening()
        self._bindings = bindings
        self._refresh_all_binding_labels()
        spinner.text = tr._("Load preset...")

    def _cancel(self) -> None:
        if self._listening_for is not None:
            self._stop_listening()
        self.dismiss()

    def _save(self) -> None:
        if self._listening_for is not None:
            self._stop_listening()
        self._on_save(self._bindings)
        self.dismiss()

    def on_dismiss(self) -> None:
        if self._listening_for is not None:
            self._stop_listening()
        if self._manager is not None:
            self._manager.paused = False


class SettingGamepadBindings(SettingItem):
    """Settings widget that opens the gamepad bindings configuration popup."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.size_hint_y = None
        self.height = dp(60)

        wrapper = AnchorLayout(anchor_y="center", anchor_x="left")

        inner = BoxLayout(
            orientation="horizontal", spacing=dp(10), size_hint=(1, None), height=dp(40), padding=[dp(10), 0]
        )

        self.summary_label = Label(text=self._get_summary(), halign="left", valign="middle", size_hint=(1, 1))
        self.summary_label.bind(size=lambda w, s: setattr(w, "text_size", s))

        btn = Button(text=tr._("Configure..."), size_hint=(None, 1), width=dp(130))
        btn.bind(on_release=self._open_popup)

        inner.add_widget(self.summary_label)
        inner.add_widget(btn)
        wrapper.add_widget(inner)
        self.add_widget(wrapper)

    def _get_summary(self) -> str:
        raw = self.value if hasattr(self, "value") else ""
        if not raw:
            return tr._("Default (Xbox 360)")
        try:
            json.loads(raw)
            return tr._("Custom")
        except Exception:
            return tr._("Default (Xbox 360)")

    def _open_popup(self, *args) -> None:
        try:
            current = json.loads(self.value) if self.value else None
        except Exception:
            current = None
        if current is None:
            current = gamepad_module.default_bindings()

        manager = None
        try:
            pendant = App.get_running_app().root.pendant
            if hasattr(pendant, "_manager"):
                manager = pendant._manager
        except Exception:
            pass

        popup = GamepadBindingsPopup(current_bindings=current, on_save=self._save_bindings, manager=manager)
        popup.open()

    def _save_bindings(self, bindings: dict) -> None:
        if bindings == gamepad_module.default_bindings():
            new_value = ""
        else:
            new_value = json.dumps(bindings)

        self.panel.set_value(self.section, self.key, new_value)
        self.value = new_value

    def on_value(self, instance, value):
        if hasattr(self, "summary_label"):
            self.summary_label.text = self._get_summary()

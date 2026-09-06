import logging

from kivy.clock import Clock
from kivy.uix.modalview import ModalView

from carveracontroller.translation import tr

from ... import Controller
from .operations.Angle.AngleOperationType import AngleOperationType
from .operations.Angle.AngleSettings import AngleSettings
from .operations.Bore.BoreOperationType import BoreOperationType
from .operations.Bore.BoreSettings import BoreSettings
from .operations.Boss.BossOperationType import BossOperationType
from .operations.Boss.BossSettings import BossSettings
from .operations.Calibration.CalibrationOperationType import CalibrationOperationType
from .operations.Calibration.CalibrationSettings import CalibrationSettings
from .operations.ConfigUtils import get_machine_config_hint
from .operations.FourthAxis.FourthAxisOperationType import FourthAxisOperationType
from .operations.FourthAxis.FourthAxisSettings import FourthAxisSettings
from .operations.InsideCorner.InsideCornerOperationType import InsideCornerOperationType
from .operations.InsideCorner.InsideCornerSettings import InsideCornerSettings
from .operations.OperationsBase import OperationsBase
from .operations.OutsideCorner.OutsideCornerOperationType import OutsideCornerOperationType
from .operations.OutsideCorner.OutsideCornerSettings import OutsideCornerSettings
from .operations.ProbeTip.ProbeTipOperationType import ProbeTipOperationType
from .operations.ProbeTip.ProbeTipSettings import ProbeTipSettings
from .operations.SingleAxis.SingleAxisProbeOperationType import SingleAxisProbeOperationType
from .operations.SingleAxis.SingleAxisProbeSettings import SingleAxisProbeSettings
from .operations.Z1Probing import (
    Z1_M480_PARAMS,
    Z1_SINGLE_AXIS_PARAMS,
    Z1UnsupportedOperation,
    ignored_parameters,
    z1_probing_active,
)
from .preview.ProbingPreviewPopup import ProbingPreviewPopup

logger = logging.getLogger(__name__)

from kivy.app import App


class ProbingPopup(ModalView):
    controller: Controller

    def __init__(self, controller, **kwargs):
        self.outside_corner_settings = None
        self.inside_corner_settings = None
        self.single_axis_settings = None
        self.bore_settings = None
        self.boss_settings = None
        self.angle_settings = None
        self.probeTipSettings = None
        self.calibration_settings = None
        self.fourth_axis_settings = None
        self._settings_panels = ()
        self.controller = controller

        self.preview_popup = ProbingPreviewPopup(controller)

        # wait on UI to finish loading
        Clock.schedule_once(self.delayed_bind, 0.1)

        super().__init__(**kwargs)

    def on_dismiss(self):
        App.get_running_app().root.restore_keyboard_jog_control()

    def allows_external_jog(self) -> bool:
        """Allow keyboard/pendant jog while the probing screen is open."""
        return self._is_open

    def delayed_bind(self, dt):
        self.outside_corner_settings = self.ids.outside_corner_settings
        self.inside_corner_settings = self.ids.inside_corner_settings
        self.single_axis_settings = self.ids.single_axis_settings
        self.bore_settings = self.ids.bore_settings
        self.boss_settings = self.ids.boss_settings
        self.calibration_settings = self.ids.calibration_settings_id
        self.angle_settings = self.ids.angle_settings
        self.probeTipSettings = self.ids.probeTipSettings
        self.fourth_axis_settings = self.ids.fourth_axis_settings
        self._settings_panels = (
            self.angle_settings,
            self.bore_settings,
            self.boss_settings,
            self.inside_corner_settings,
            self.outside_corner_settings,
            self.single_axis_settings,
            self.calibration_settings,
        )

    def open(self, *args, **kwargs):
        self.refresh_probe_tip_diameter_hints()
        super().open(*args, **kwargs)

    def delayed_bind_complete(self, dt):
        # self.angle_settings = self.ids.angle_settings
        # self.probeTipSettings = self.ids.probeTipSettings
        return

    def refresh_probe_tip_diameter_hints(self):
        # zprobe.probe_tip_diameter is a Community firmware config key and does
        # not exist on a Z1, so "config" would point at a setting the machine
        # has not got. Offer the firmware's own M480 default instead -- it is a
        # starting point, not a measured value; pre-travel means the effective
        # diameter is smaller than the ball.
        hint = get_machine_config_hint("zprobe.probe_tip_diameter")
        if not hint:
            hint = tr._("2 = default") if z1_probing_active() else tr._("config")
        for settings in self._settings_panels:
            if settings and "ProbeTipDiameter" in settings.ids:
                settings.ids.ProbeTipDiameter.hint_text = hint

    def on_single_axis_probing_pressed(self, operation_key: str):
        cfg = self.single_axis_settings.get_config()
        the_op = SingleAxisProbeOperationType[operation_key].value
        self.show_preview(the_op, cfg)

    def on_inside_corner_probing_pressed(self, operation_key: str):
        cfg = self.inside_corner_settings.get_config()
        the_op = InsideCornerOperationType[operation_key].value
        self.show_preview(the_op, cfg)

    def on_outside_corner_probing_pressed(self, operation_key: str):
        cfg = self.outside_corner_settings.get_config()
        the_op = OutsideCornerOperationType[operation_key].value
        self.show_preview(the_op, cfg)

    def on_bore_probing_pressed(self, operation_key: str):
        cfg = self.bore_settings.get_config()
        the_op = BoreOperationType[operation_key].value
        self.show_preview(the_op, cfg)

    def on_boss_probing_pressed(self, operation_key: str):
        cfg = self.boss_settings.get_config()
        the_op = BossOperationType[operation_key].value
        self.show_preview(the_op, cfg)

    def on_angle_probing_pressed(self, operation_key: str):
        cfg = self.angle_settings.get_config()
        the_op = AngleOperationType[operation_key].value
        self.show_preview(the_op, cfg)

    def on_probeTip_probing_pressed(self, operation_key: str):
        cfg = self.probeTipSettings.get_config()
        the_op = ProbeTipOperationType[operation_key].value
        self.show_preview(the_op, cfg)

    def on_callibration_probing_pressed(self, operation_key: str):
        cfg = self.calibration_settings.get_config()
        the_op = CalibrationOperationType[operation_key].value
        self.show_preview(the_op, cfg)

    def on_fourth_axis_probing_pressed(self, operation_key: str):
        cfg = self.fourth_axis_settings.get_config()
        the_op = FourthAxisOperationType[operation_key].value
        self.show_preview(the_op, cfg)

    def _generate_for_machine(self, operation: OperationsBase, cfg):
        """
        Build the G-code for *operation*, or explain why this machine cannot.

        Returns ``(gcode, note)``. An empty gcode means nothing will be sent --
        the preview shows the note instead. Refusing is deliberate: the Z1
        silently ignores the Community firmware's M460-M469, so emitting them
        would look like a probe cycle that simply never happens.
        """
        try:
            gcode = operation.generate(cfg)
        except Z1UnsupportedOperation:
            return "", tr._("This machine's firmware has no equivalent for this probing operation.")

        if not z1_probing_active():
            return gcode, ""

        if gcode.startswith("M480"):
            supported = Z1_M480_PARAMS
            # M480 always writes the work offset and returns to it; the
            # Community firmware's S option to probe without zeroing has no
            # equivalent here.
            origin_note = tr._("This sets the work origin and finishes at X0 Y0.")
        elif gcode.startswith("G38."):
            supported = Z1_SINGLE_AXIS_PARAMS
            origin_note = (
                tr._("This sets the work origin on this axis.")
                if "G10 L20" in gcode
                else tr._("This measures only and does not change the work origin.")
            )
        else:
            return "", tr._("This machine's firmware has no equivalent for this probing operation.")

        note = ""
        ignored = ignored_parameters(cfg, supported)
        if ignored:
            note = "\n\n" + tr._("Ignored on this machine: ") + ", ".join(ignored)
        note += "\n" + origin_note
        return gcode, note

    def show_preview(self, operation: OperationsBase, cfg):
        missing_definition = operation.get_missing_config(cfg)

        if missing_definition is not None:
            self.preview_popup.gcode = ""
            self.preview_popup.probe_preview_label = "Missing required parameter " + missing_definition.label
        else:
            gcode, note = self._generate_for_machine(operation, cfg)
            self.preview_popup.gcode = gcode
            self.preview_popup.probe_preview_label = gcode + note if gcode else note

        self.preview_popup.open()

        Clock.schedule_once(lambda dt: self.link_shared_data_with_refresh(self.preview_popup), 0.1)

    def link_shared_data_with_refresh(self, popup):
        app = App.get_running_app()
        app.mdi_data.clear()
        try:
            popup.ids.manual_rvPopup.data = app.mdi_data
        except IndexError:
            logger.error("Recycle view layout change ignored")

        app.bind(mdi_data=lambda instance, value: self.on_mdi_data_changed(popup))

    def on_mdi_data_changed(self, popup):
        try:
            popup.ids.manual_rvPopup.refresh_from_data()
            Clock.schedule_once(lambda dt: self.scroll_to_bottom(popup.ids.manual_rvPopup), 0.01)
        except Exception as e:
            print("Popup refresh failed:", e)

    def scroll_to_bottom(self, rv):
        try:
            Clock.schedule_once(lambda dt: setattr(rv, "scroll_y", 0), 0.01)
        except Exception as e:
            print("Scroll failed:", e)

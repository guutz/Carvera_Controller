"""
Tests for the pre-run coordinate-system check.

The question it answers is "is the origin I just probed the one this job will
use, and will anything quietly replace it" -- so the cases that matter are a
file selecting a different WCS than the active one, and a Config-and-Run step
that ends in G10 L20 P0.
"""

import pytest

from carveracontroller.Controller import Controller


@pytest.fixture
def controller():
    return Controller.__new__(Controller)


class TestScanWcsSelections:
    def test_finds_selections_in_file_order(self, controller):
        lines = ["G21 G90\n", "G54 G17\n", "G0 X1\n", "G55\n"]
        assert controller.scan_wcs_selections(lines) == [("G54", 2), ("G55", 4)]

    def test_ignores_selections_inside_comments(self, controller):
        """A commented-out selection does not change what the machine does."""
        lines = ["(use G55 for the second op)\n", "; G56 was here\n", "G54\n"]
        assert controller.scan_wcs_selections(lines) == [("G54", 3)]

    def test_does_not_read_g59_1_as_g59(self, controller):
        assert controller.scan_wcs_selections(["G59.1\n"]) == [("G59.1", 1)]

    def test_finds_a_selection_sharing_a_line(self, controller):
        assert controller.scan_wcs_selections(["G90 G54 G17\n"]) == [("G54", 1)]

    def test_empty_file_selects_nothing(self, controller):
        assert controller.scan_wcs_selections([]) == []
        assert controller.scan_wcs_selections(None) == []


class TestPreRunWarnings:
    def test_silent_when_the_file_matches_the_active_system(self, controller):
        assert controller.pre_run_warnings(["G54\n"], "G54", {}) == []

    def test_silent_when_the_file_selects_nothing(self, controller):
        """No selection means the file inherits the active WCS, which is fine."""
        assert controller.pre_run_warnings(["G0 X1\n"], "G54", {}) == []

    def test_flags_a_file_that_selects_another_system(self, controller):
        assert controller.pre_run_warnings(["G55\n"], "G54", {}) == ["wcs_mismatch"]

    def test_flags_a_file_that_switches_between_systems(self, controller):
        warnings = controller.pre_run_warnings(["G54\n", "G55\n"], "G54", {})
        assert warnings == ["wcs_multiple"]

    def test_flags_the_z_probe_step_that_rewrites_the_origin(self, controller):
        """fill_zprobe_scripts ends in G10 L20 P0 Z..., replacing the probed zero."""
        config = {"zprobe": {"active": True}}
        assert controller.pre_run_warnings(["G54\n"], "G54", config) == ["zprobe_overwrites_z"]

    @pytest.mark.parametrize("step", ["margin", "leveling"])
    def test_steps_that_do_not_touch_the_origin_are_not_flagged(self, controller, step):
        """
        Neither fill_margin_scripts nor fill_autolevel_scripts contains a G10, so
        warning about them would train the user to dismiss the dialog.
        """
        config = {step: {"active": True}}
        assert controller.pre_run_warnings(["G54\n"], "G54", config) == []

    def test_reports_both_kinds_together(self, controller):
        config = {"zprobe": {"active": True}}
        warnings = controller.pre_run_warnings(["G55\n"], "G54", config)
        assert warnings == ["wcs_mismatch", "zprobe_overwrites_z"]

    def test_no_active_wcs_yet_does_not_claim_a_mismatch(self, controller):
        """Before the machine reports a status there is nothing to compare against."""
        assert controller.pre_run_warnings(["G55\n"], "", {}) == []


class TestPlayDefersToTheCheck:
    """
    The dialog has to come before apply(), which buffers the margin/probe/level
    sequence to the machine. Confirming after that point would be asking about
    commands already handed over.
    """

    @staticmethod
    def _play(start_line, enabled=True):
        from types import SimpleNamespace

        from carveracontroller.main import Makera

        calls = SimpleNamespace(popup=[], started=[])
        fake = SimpleNamespace(
            _pre_run_check_enabled=lambda: enabled,
            open_pre_run_check_popup=lambda f: calls.popup.append(f),
            _start_play=lambda f, s: calls.started.append((f, s)),
        )
        Makera.play(fake, "job.nc", start_line)
        return calls

    def test_normal_run_asks_first_and_does_not_start(self):
        calls = self._play(None)
        assert calls.popup == ["job.nc"]
        assert calls.started == []

    def test_starts_directly_when_the_check_is_disabled(self):
        calls = self._play(None, enabled=False)
        assert calls.popup == []
        assert calls.started == [("job.nc", None)]

    def test_resume_is_not_double_prompted(self):
        """Resume has its own confirmation showing the whole recovery sequence."""
        calls = self._play("120")
        assert calls.popup == []
        assert calls.started == [("job.nc", "120")]

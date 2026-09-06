"""
Tests for the live-state checks on the resume recovery sequence.

These compare the reconstruction against what the machine reports; they do not
replace it. The preamble's job is to recreate the modal state the *file* had
established by the resume line, and a probe cycle or MDI command in between can
leave the machine somewhere else entirely. Live state only decides what happens
where the file establishes nothing.
"""

import pytest

from carveracontroller.CNC import CNC
from carveracontroller.Controller import Controller

ABSOLUTE, RELATIVE = 1, 0
INCHES, MM = 1, 0


@pytest.fixture
def controller():
    return Controller.__new__(Controller)


@pytest.fixture(autouse=True)
def restore_modes():
    """CNC.vars is process-global; put the modal flags back after each test."""
    saved = {k: CNC.vars.get(k) for k in ("absolute_mode", "inch_mode")}
    yield
    CNC.vars.update(saved)


# A recovery sequence with everything else present, so only the mode keys vary.
COMPLETE = ["buffer T1 M6", "buffer M3 S9000", "buffer G1 F600"]


class TestRelativeMode:
    def test_flags_a_machine_left_in_g91(self, controller):
        """A file of absolute moves resumed in G91 treats each coordinate as an offset."""
        CNC.vars["absolute_mode"] = RELATIVE
        assert "relative_mode" in controller.resume_playback_warnings(COMPLETE)

    def test_silent_when_the_machine_is_absolute(self, controller):
        CNC.vars["absolute_mode"] = ABSOLUTE
        assert "relative_mode" not in controller.resume_playback_warnings(COMPLETE)

    def test_silent_when_the_sequence_restores_the_mode(self, controller):
        """If the file established G90, the preamble replays it and live state is moot."""
        CNC.vars["absolute_mode"] = RELATIVE
        commands = COMPLETE + ["buffer G90"]
        assert "relative_mode" not in controller.resume_playback_warnings(commands)

    def test_silent_when_the_machine_has_not_reported_yet(self, controller):
        """Absent is not evidence of relative mode."""
        CNC.vars.pop("absolute_mode", None)
        assert "relative_mode" not in controller.resume_playback_warnings(COMPLETE)


class TestUnitMismatch:
    def test_flags_an_inch_machine_running_a_metric_file(self, controller):
        CNC.vars["inch_mode"] = INCHES
        assert "unit_mismatch" in controller.resume_playback_warnings(COMPLETE, document_unit="mm")

    def test_flags_a_metric_machine_running_an_inch_file(self, controller):
        CNC.vars["inch_mode"] = MM
        assert "unit_mismatch" in controller.resume_playback_warnings(COMPLETE, document_unit="in")

    def test_silent_when_they_agree(self, controller):
        CNC.vars["inch_mode"] = MM
        assert "unit_mismatch" not in controller.resume_playback_warnings(COMPLETE, document_unit="mm")

    def test_silent_when_the_sequence_restores_the_units(self, controller):
        CNC.vars["inch_mode"] = INCHES
        commands = COMPLETE + ["buffer G21"]
        assert "unit_mismatch" not in controller.resume_playback_warnings(commands, document_unit="mm")

    def test_silent_without_a_known_document_unit(self, controller):
        CNC.vars["inch_mode"] = INCHES
        assert "unit_mismatch" not in controller.resume_playback_warnings(COMPLETE)

    def test_silent_on_the_unknown_sentinel(self, controller):
        """The status parser already treats 999 as 'not reported'."""
        CNC.vars["inch_mode"] = 999
        assert "unit_mismatch" not in controller.resume_playback_warnings(COMPLETE, document_unit="mm")


class TestExistingWarningsStillWork:
    def test_empty_sequence_reports_the_original_three(self, controller):
        assert controller.resume_playback_warnings([]) == ["tool_change", "feed", "spindle_speed"]

    def test_complete_sequence_is_silent(self, controller):
        CNC.vars["absolute_mode"] = ABSOLUTE
        CNC.vars["inch_mode"] = MM
        assert controller.resume_playback_warnings(COMPLETE, document_unit="mm") == []

    def test_laser_mode_does_not_need_a_spindle_speed(self, controller):
        CNC.vars["absolute_mode"] = ABSOLUTE
        commands = ["buffer T1 M6", "buffer M321", "buffer G1 F600"]
        assert "spindle_speed" not in controller.resume_playback_warnings(commands)

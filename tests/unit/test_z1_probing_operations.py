"""
Tests for translating probing operations onto the Makera Z1's M480 family.

The stakes here are physical: an operation that probes the wrong quadrant
drives the probe into the workpiece. The two firmwares agree on X but use
opposite Y conventions, so the corner tables are asserted explicitly rather
than derived from the operation's own direction flags.
"""

import pytest

from carveracontroller.addons.probing.operations.Bore.BoreOperationType import BoreOperationType
from carveracontroller.addons.probing.operations.Boss.BossOperationType import BossOperationType
from carveracontroller.addons.probing.operations.InsideCorner.InsideCornerOperationType import (
    InsideCornerOperationType,
)
from carveracontroller.addons.probing.operations.OutsideCorner.OutsideCornerOperationType import (
    OutsideCornerOperationType,
)
from carveracontroller.addons.probing.operations.Z1Probing import (
    Z1UnsupportedOperation,
    generate_m480,
    ignored_parameters,
    z1_probing_active,
)
from carveracontroller.CNC import CNC, PROBE_3D_TOOL_NUMBER, Z1_PROBE_3D_TOOL_NUMBER


@pytest.fixture
def on_z1():
    original = CNC.probe_3d_tool
    CNC.probe_3d_tool = Z1_PROBE_3D_TOOL_NUMBER
    yield
    CNC.probe_3d_tool = original


@pytest.fixture
def on_carvera():
    original = CNC.probe_3d_tool
    CNC.probe_3d_tool = PROBE_3D_TOOL_NUMBER
    yield
    CNC.probe_3d_tool = original


BASE = {"X": "20", "Y": "15", "D": "3", "E": "2"}


class TestMachineDetection:
    def test_active_only_for_the_z1_probe(self, on_z1):
        assert z1_probing_active()

    def test_inactive_on_a_carvera(self, on_carvera):
        assert not z1_probing_active()


class TestOutsideCornerQuadrants:
    """
    TopLeft = left + back, TopRight = right + back,
    BottomRight = right + front, BottomLeft = left + front.
    """

    @pytest.mark.parametrize(
        ("operation", "subcode"),
        [("TopLeft", "1"), ("TopRight", "2"), ("BottomRight", "3"), ("BottomLeft", "4")],
    )
    def test_maps_to_the_matching_subcode(self, on_z1, operation, subcode):
        gcode = OutsideCornerOperationType[operation].value.generate(dict(BASE))
        assert gcode.startswith(f"M480.{subcode} ")

    def test_carvera_still_emits_the_community_code(self, on_carvera):
        gcode = OutsideCornerOperationType["TopLeft"].value.generate(dict(BASE))
        assert gcode.startswith("M464")


class TestInsideCornerQuadrants:
    @pytest.mark.parametrize(
        ("operation", "subcode"),
        [("TopLeft", "5"), ("TopRight", "6"), ("BottomRight", "7"), ("BottomLeft", "8")],
    )
    def test_maps_to_the_matching_subcode(self, on_z1, operation, subcode):
        gcode = InsideCornerOperationType[operation].value.generate(dict(BASE))
        assert gcode.startswith(f"M480.{subcode} ")

    def test_shares_quadrants_with_the_outside_corners(self, on_z1):
        """Inside subcode = outside subcode + 4, for the same named corner."""
        for name in ("TopLeft", "TopRight", "BottomRight", "BottomLeft"):
            outside = OutsideCornerOperationType[name].value.generate(dict(BASE))
            inside = InsideCornerOperationType[name].value.generate(dict(BASE))
            out_n = int(outside.split()[0].split(".")[1])
            in_n = int(inside.split()[0].split(".")[1])
            assert in_n == out_n + 4

    def test_carvera_still_emits_the_community_code(self, on_carvera):
        assert InsideCornerOperationType["TopLeft"].value.generate(dict(BASE)).startswith("M463")


class TestParameterMapping:
    def test_maps_depth_e_onto_m480_z(self, on_z1):
        """The screen's E ('below the top surface') is M480's Z."""
        assert generate_m480("1", {"X": "20", "Y": "15", "D": "3", "E": "2"}) == "M480.1 D3 X20 Y15 Z2"

    def test_omits_parameters_that_are_not_set(self, on_z1):
        assert generate_m480("9", {"X": "20", "Y": "15"}) == "M480.9 X20 Y15"

    @pytest.mark.parametrize("raw", ["-20", "20"])
    def test_distances_are_sent_as_magnitudes(self, raw):
        """
        M480 applies the quadrant sign itself, so a negative input would
        double-negate and probe the opposite corner.
        """
        assert generate_m480("3", {"X": raw, "Y": raw}) == "M480.3 X20 Y20"

    def test_reports_parameters_it_cannot_honour(self):
        assert ignored_parameters({"X": "20", "H": "5", "L": "1"}) == ["H", "L"]

    def test_blank_parameters_are_not_reported_as_ignored(self):
        assert ignored_parameters({"X": "20", "H": "", "L": "  "}) == []


class TestUnsupportedOperations:
    @pytest.mark.parametrize("operation", ["CenterX", "CenterY"])
    def test_single_axis_bore_has_no_equivalent(self, on_z1, operation):
        """M480.9 always centres both axes; running it for one would move the other."""
        with pytest.raises(Z1UnsupportedOperation):
            BoreOperationType[operation].value.generate(dict(BASE))

    @pytest.mark.parametrize("operation", ["CenterX", "CenterY"])
    def test_single_axis_boss_has_no_equivalent(self, on_z1, operation):
        with pytest.raises(Z1UnsupportedOperation):
            BossOperationType[operation].value.generate(dict(BASE))

    @pytest.mark.parametrize("operation", ["CenterBore", "CenterPocket"])
    def test_two_axis_bore_maps(self, on_z1, operation):
        assert BoreOperationType[operation].value.generate(dict(BASE)).startswith("M480.9 ")

    @pytest.mark.parametrize("operation", ["CenterBoss", "CenterBlock"])
    def test_two_axis_boss_maps(self, on_z1, operation):
        assert BossOperationType[operation].value.generate(dict(BASE)).startswith("M480.10 ")

    def test_single_axis_still_works_on_a_carvera(self, on_carvera):
        assert BoreOperationType["CenterX"].value.generate(dict(BASE)).startswith("M461")


class TestPreviewGuard:
    """
    The preview is the last stop before a move runs, so it refuses anything
    that would reach a Z1 as a command it silently discards.
    """

    @staticmethod
    def _generate(operation, cfg):
        from carveracontroller.addons.probing.ProbingPopup import ProbingPopup

        # _generate_for_machine touches nothing on self.
        return ProbingPopup._generate_for_machine(None, operation, cfg)

    def test_refuses_an_unmapped_community_operation(self, on_z1):
        """
        The 4th axis operation here is M465.1, "4th Axis Level". The Z1's
        own 4th axis probing is a different thing entirely -- a headstock Z
        probe offset to the rotation centreline, driven from Config and Run --
        so there is nothing to map it onto.
        """
        from carveracontroller.addons.probing.operations.FourthAxis.FourthAxisOperationType import (
            FourthAxisOperationType,
        )

        gcode, note = self._generate(FourthAxisOperationType.Level.value, {"D": "3"})
        assert gcode == ""
        assert "no equivalent" in note

    def test_passes_a_mapped_operation_through(self, on_z1):
        gcode, note = self._generate(OutsideCornerOperationType["TopLeft"].value, dict(BASE))
        assert gcode.startswith("M480.1 ")
        assert "work origin" in note

    def test_names_parameters_it_cannot_honour(self, on_z1):
        cfg = dict(BASE)
        cfg["L"] = "1"
        gcode, note = self._generate(OutsideCornerOperationType["TopLeft"].value, cfg)
        assert gcode.startswith("M480.1 ")
        assert "L" in note

    def test_carvera_is_untouched(self, on_carvera):
        gcode, note = self._generate(OutsideCornerOperationType["TopLeft"].value, dict(BASE))
        assert gcode.startswith("M464")
        assert note == ""

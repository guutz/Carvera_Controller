"""
Tests for model-aware 3D probe tool selection.

The Community firmware selects its 3D probe from the T999990-T999999 probe
range; Makera Z1/Z1Pro stock firmware uses a single sentinel, T9999, which sits
outside that range. Picking the wrong one is not cosmetic: on a Z1, selecting
the stock probe (T0) runs the Carvera wireless-probe liveness check and halts
the machine with "Probe dead or not set".
"""

import pytest

from carveracontroller.CNC import (
    CNC,
    PROBE_3D_TOOL_NUMBER,
    Z1_PROBE_3D_TOOL_NUMBER,
    is_3d_probe_tool,
    is_probe_tools_range,
    is_probing_tool,
    probe_3d_tool_for_model,
)


@pytest.fixture(autouse=True)
def restore_probe_tool():
    """CNC.probe_3d_tool is process-global state; put it back after each test."""
    original = CNC.probe_3d_tool
    yield
    CNC.probe_3d_tool = original


class TestProbeToolForModel:
    @pytest.mark.parametrize("model", ["Z1", "Z1P", "Z1Pro"])
    def test_z1_family_uses_the_stock_sentinel(self, model):
        assert probe_3d_tool_for_model(model) == Z1_PROBE_3D_TOOL_NUMBER

    @pytest.mark.parametrize("model", ["C1", "CA1"])
    def test_carvera_uses_the_community_probe_range(self, model):
        assert probe_3d_tool_for_model(model) == PROBE_3D_TOOL_NUMBER

    @pytest.mark.parametrize("model", ["", None])
    def test_unknown_model_falls_back_to_the_community_number(self, model):
        """Before a machine reports its model, assume the pre-existing behaviour."""
        assert probe_3d_tool_for_model(model) == PROBE_3D_TOOL_NUMBER


class TestIs3dProbeTool:
    def test_matches_the_connected_model_probe(self):
        CNC.probe_3d_tool = Z1_PROBE_3D_TOOL_NUMBER
        assert is_3d_probe_tool(Z1_PROBE_3D_TOOL_NUMBER)

    def test_other_probe_range_tools_are_not_the_3d_probe(self):
        """Custom probes share the range but display as generic probes."""
        CNC.probe_3d_tool = PROBE_3D_TOOL_NUMBER
        assert not is_3d_probe_tool(999995)
        assert not is_3d_probe_tool(999999)

    def test_z1_sentinel_is_an_ordinary_tool_on_a_carvera(self):
        """T9999 must not be mistaken for a probe when a Carvera is connected."""
        CNC.probe_3d_tool = PROBE_3D_TOOL_NUMBER
        assert not is_3d_probe_tool(Z1_PROBE_3D_TOOL_NUMBER)

    @pytest.mark.parametrize("value", [None, "", "abc"])
    def test_non_numeric_is_not_a_probe(self, value):
        assert not is_3d_probe_tool(value)


class TestIsProbingTool:
    def test_covers_the_whole_community_probe_range(self):
        CNC.probe_3d_tool = PROBE_3D_TOOL_NUMBER
        assert is_probing_tool(PROBE_3D_TOOL_NUMBER)
        assert is_probing_tool(999995)

    def test_covers_the_z1_sentinel_when_a_z1_is_connected(self):
        """Without this the probing screen refuses to open on a Z1."""
        CNC.probe_3d_tool = Z1_PROBE_3D_TOOL_NUMBER
        assert is_probing_tool(Z1_PROBE_3D_TOOL_NUMBER)
        assert not is_probe_tools_range(Z1_PROBE_3D_TOOL_NUMBER)

    def test_ordinary_tools_are_not_probing_tools(self):
        CNC.probe_3d_tool = Z1_PROBE_3D_TOOL_NUMBER
        for tool in (1, 6, 8888):
            assert not is_probing_tool(tool)

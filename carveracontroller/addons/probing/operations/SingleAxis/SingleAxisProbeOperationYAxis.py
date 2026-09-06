import copy

from carveracontroller.addons.probing.operations.OperationsBase import OperationsBase, ProbeSettingDefinition
from carveracontroller.addons.probing.operations.SingleAxis.SingleAxisProbeParameterDefinitions import (
    SingleAxisProbeParameterDefinitions,
)
from carveracontroller.addons.probing.operations.Z1Probing import (
    generate_single_axis,
    probe_tip_diameter_required,
    z1_probing_active,
)


class SingleAxisProbeOperationYAxis(OperationsBase):
    imagePath: str

    def __init__(self, title, y_is_negative_move, image_path, **kwargs):
        self.title = title
        self.imagePath = image_path
        self.y_is_negative_move = y_is_negative_move

    def generate(self, input_config: dict[str, float]):
        if z1_probing_active():
            # No Z1 macro for a single axis; generated from G38.2 primitives.
            return generate_single_axis("Y", self.y_is_negative_move, input_config)

        config = copy.deepcopy(input_config)

        # remove other axes for clarity
        config[SingleAxisProbeParameterDefinitions.XAxisDistance.code] = ""
        config[SingleAxisProbeParameterDefinitions.ZAxisDistance.code] = ""

        super().apply_direction(SingleAxisProbeParameterDefinitions.YAxisDistance.code, config, self.y_is_negative_move)

        return "M466" + self.config_to_gcode(config)

    def get_missing_config(self, config: dict[str, float]):
        if z1_probing_active() and probe_tip_diameter_required("Y", config):
            return SingleAxisProbeParameterDefinitions.ProbeTipDiameter
        definition = SingleAxisProbeParameterDefinitions.YAxisDistance
        if not definition.code in config or len(config[definition.code]) == 0:
            return definition
        return None

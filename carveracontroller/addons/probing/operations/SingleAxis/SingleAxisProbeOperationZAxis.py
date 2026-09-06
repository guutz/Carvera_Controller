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


class SingleAxisProbeOperationZAxis(OperationsBase):
    imagePath: str

    def __init__(self, title, image_path, **kwargs):
        self.title = title
        self.imagePath = image_path

    def generate(self, input_config: dict[str, float]):
        if z1_probing_active():
            # No Z1 macro for a single axis; generated from G38.2 primitives.
            return generate_single_axis("Z", True, input_config)

        config = copy.deepcopy(input_config)

        # remove other axes for clarity
        config[SingleAxisProbeParameterDefinitions.XAxisDistance.code] = ""
        config[SingleAxisProbeParameterDefinitions.YAxisDistance.code] = ""

        super().apply_direction(SingleAxisProbeParameterDefinitions.ZAxisDistance.code, config, True)

        return "M466" + self.config_to_gcode(config)

    def get_missing_config(self, config: dict[str, float]):
        if z1_probing_active() and probe_tip_diameter_required("Z", config):
            return SingleAxisProbeParameterDefinitions.ProbeTipDiameter
        print(config)
        definition = SingleAxisProbeParameterDefinitions.ZAxisDistance
        if not definition.code in config or len(config[definition.code]) == 0:
            return definition
        return None

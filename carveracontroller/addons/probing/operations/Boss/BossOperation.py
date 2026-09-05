import copy

from carveracontroller.addons.probing.operations.Boss.BossParameterDefinitions import BossParameterDefinitions
from carveracontroller.addons.probing.operations.OperationsBase import OperationsBase, ProbeSettingDefinition
from carveracontroller.addons.probing.operations.SingleAxis.SingleAxisProbeParameterDefinitions import (
    SingleAxisProbeParameterDefinitions,
)
from carveracontroller.addons.probing.operations.Z1Probing import (
    Z1UnsupportedOperation,
    generate_m480,
    z1_probing_active,
)


class BossOperation(OperationsBase):
    imagePath: str

    def __init__(self, title, requires_x, requires_y, image_path, z1_subcode=None, **kwargs):
        self.title = title
        self.imagePath = image_path
        self.requires_x = requires_x
        self.requires_y = requires_y
        self.z1_subcode = z1_subcode

    def generate(self, input_config: dict[str, float]):
        if z1_probing_active():
            # M480.{9,10} always centre on both axes, so the single-axis
            # variants have no equivalent and must not silently run a 2-axis move.
            if self.z1_subcode is None:
                raise Z1UnsupportedOperation(self.title)
            return generate_m480(self.z1_subcode, input_config)

        config = copy.deepcopy(input_config)

        if not self.requires_x:
            config[BossParameterDefinitions.XAxisDistance.code] = ""
        if not self.requires_y:
            config[BossParameterDefinitions.YAxisDistance.code] = ""

        return "M462" + self.config_to_gcode(config)

    def get_missing_config(self, config: dict[str, float]):
        if self.requires_x:
            definition = BossParameterDefinitions.XAxisDistance
            if definition.code not in config or not config[definition.code].strip():
                return definition
        if self.requires_y:
            definition = BossParameterDefinitions.YAxisDistance
            if definition.code not in config or not config[definition.code].strip():
                return definition
        required_definitions = {
            name: value
            for name, value in BossParameterDefinitions.__dict__.items()
            if isinstance(value, ProbeSettingDefinition) and value.is_required
        }

        return super().validate_required(required_definitions, config)

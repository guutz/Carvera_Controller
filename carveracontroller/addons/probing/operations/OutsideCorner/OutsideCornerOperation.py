import copy

from carveracontroller.addons.probing.operations.OperationsBase import OperationsBase, ProbeSettingDefinition
from carveracontroller.addons.probing.operations.OutsideCorner.OutsideCornerParameterDefinitions import (
    OutsideCornerParameterDefinitions,
)
from carveracontroller.addons.probing.operations.Z1Probing import (
    Z1UnsupportedOperation,
    generate_m480,
    z1_probing_active,
)


class OutsideCornerOperation(OperationsBase):
    title: str
    image_path: str
    description: str
    imagePath: str

    def __init__(self, title, x_is_negative_move, y_is_negative_move, image_path, z1_subcode=None, **kwargs):
        self.title = title
        self.image_path = image_path
        self.x_is_negative_move = x_is_negative_move
        self.y_is_negative_move = y_is_negative_move
        self.z1_subcode = z1_subcode

    def generate(self, input_config: dict[str, float]):
        if z1_probing_active():
            if self.z1_subcode is None:
                raise Z1UnsupportedOperation(self.title)
            # Signed before apply_direction: M480 derives the quadrant from the
            # subcode and applies the signs itself.
            return generate_m480(self.z1_subcode, input_config)

        config = copy.deepcopy(input_config)

        super().apply_direction(OutsideCornerParameterDefinitions.XAxisDistance.code, config, self.x_is_negative_move)

        super().apply_direction(OutsideCornerParameterDefinitions.YAxisDistance.code, config, self.y_is_negative_move)

        return "M464" + self.config_to_gcode(config)

    def get_missing_config(self, config: dict[str, float]):
        required_definitions = {
            name: value
            for name, value in OutsideCornerParameterDefinitions.__dict__.items()
            if isinstance(value, ProbeSettingDefinition) and value.is_required
        }

        return super().validate_required(required_definitions, config)

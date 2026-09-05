from enum import Enum

from carveracontroller.addons.probing.operations.Bore.BoreOperation import BoreOperation

# M480.9 centres on both axes at once, so only the two-axis variants map. The
# single-axis ones stay unmapped rather than quietly probing the other axis too.


class BoreOperationType(Enum):
    CenterX = BoreOperation("Bore - Center X", True, False, "")
    CenterY = BoreOperation("Bore - Center Y", False, True, "")
    CenterBore = BoreOperation("Bore - Center Bore", True, True, "", z1_subcode="9")
    CenterPocket = BoreOperation("Bore - Center Pocket", True, True, "", z1_subcode="9")

from enum import Enum

from carveracontroller.addons.probing.operations.Boss.BossOperation import BossOperation

# M480.10 centres on both axes at once; see BoreOperationType.


class BossOperationType(Enum):
    CenterX = BossOperation("Boss - Center X", True, False, "")
    CenterY = BossOperation("Boss - Center Y", False, True, "")
    CenterBoss = BossOperation("Boss - Center Boss", True, True, "", z1_subcode="10")
    CenterBlock = BossOperation("Boss - Center Block", True, True, "", z1_subcode="10")

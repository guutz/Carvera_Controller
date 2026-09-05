from enum import Enum

from carveracontroller.addons.probing.operations.OutsideCorner.OutsideCornerOperation import OutsideCornerOperation

# z1_subcode maps each corner onto the Z1's M480 outside-corner subcodes. The
# pairing is by physical corner, not by sign: the two firmwares use opposite Y
# conventions. See Z1Probing for the derivation.
#   TopLeft = left + back, TopRight = right + back,
#   BottomRight = right + front, BottomLeft = left + front


class OutsideCornerOperationType(Enum):
    TopLeft = OutsideCornerOperation("Outside Corner - Top Left", False, True, "", z1_subcode="1")
    TopRight = OutsideCornerOperation("Outside Corner - Top Right", True, True, "", z1_subcode="2")
    BottomRight = OutsideCornerOperation("Outside Corner - Bottom Right", True, False, "", z1_subcode="3")
    BottomLeft = OutsideCornerOperation("Outside Corner - Bottom Left", False, False, "", z1_subcode="4")

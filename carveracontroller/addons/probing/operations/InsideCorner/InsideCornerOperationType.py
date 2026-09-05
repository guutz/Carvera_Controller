from enum import Enum

from carveracontroller.addons.probing.operations.InsideCorner.InsideCornerOperation import InsideCornerOperation

# Same quadrants as the outside corners, on the Z1's M480 subcodes 5-8.


class InsideCornerOperationType(Enum):
    TopLeft = InsideCornerOperation("Inside Corner - Top Left", False, True, "", z1_subcode="5")
    TopRight = InsideCornerOperation("Inside Corner - Top Right", True, True, "", z1_subcode="6")
    BottomRight = InsideCornerOperation("Inside Corner - Bottom Right", True, False, "", z1_subcode="7")
    BottomLeft = InsideCornerOperation("Inside Corner - Bottom Left", False, False, "", z1_subcode="8")

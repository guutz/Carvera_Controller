"""
Translate the probing screen's operations into the Makera Z1's M480 family.

The Community firmware exposes probing as M460-M469, which Z1/Z1Pro stock
firmware does not implement at all. It has its own suite instead -- M480
subcodes, plus M495.3 -- reached with a different parameter set and, on one
axis, the opposite sign convention.

Sign conventions
----------------
Both firmwares agree on X: a positive X distance moves -X and then probes +X,
so it finds the face or wall at the *lower* X ("left").

They disagree on Y. Community M464 moves -Y and probes +Y for a positive Y
distance, finding the *front* face. Z1 M480 moves +Y and probes -Y for a
positive Y distance, finding the *back* face. A Community Y is therefore a Z1
-Y, which is why the corner tables below are not a straight copy of the
operation's own (x_is_negative, y_is_negative) flags.

Rather than carry that inversion around, each operation names its M480 subcode
directly and passes X/Y as magnitudes: M480 derives the quadrant from the
subcode and applies the signs itself (subcode 2 negates X, subcode 3 negates
both, and so on), so a caller that also signed them would double-negate.

Corner quadrants, derived from ATCHandler.cpp fill_OutCorner_scripts and
fill_InCorner_scripts -- both resolve to the same quadrant for a given pair of
signs, so outside and inside share one table:

    X+ -> left face/wall      Y+ -> back face/wall
    X- -> right face/wall     Y- -> front face/wall

Parameters
----------
M480 accepts only D (probe tip diameter), X, Y and Z (how far below the top
surface to probe from the side). The probing screen offers considerably more.
Anything else the user has filled in is *not* silently dropped -- it is
reported by ignored_parameters() so the preview can say so before the move
runs.

One semantic difference has no mapping at all: M480 always writes the work
offset (G10 L20 P0) and finishes at the new X0 Y0. The Community operations
make that optional via S. A Z1 probe cycle therefore always sets the WCS.
"""

from carveracontroller.CNC import CNC, Z1_PROBE_3D_TOOL_NUMBER

# G-code letters M480 understands, in emission order.
Z1_SUPPORTED_PARAMS = ("D", "X", "Y", "Z")

# Probing-screen parameter codes that map onto an M480 letter.
#   D (tip diameter) and X/Y (probe distances) keep their letter.
#   E ("how far below the top surface ... to probe on each side") is M480's Z.
Z1_PARAM_SOURCES = {"D": "D", "X": "X", "Y": "Y", "Z": "E"}

# Outside corner: probing-screen operation name -> M480 subcode.
Z1_OUTSIDE_CORNER_SUBCODES = {
    "TopLeft": "1",  # left + back
    "TopRight": "2",  # right + back
    "BottomRight": "3",  # right + front
    "BottomLeft": "4",  # left + front
}

# Inside corner: same quadrants, subcodes 5-8.
Z1_INSIDE_CORNER_SUBCODES = {
    "TopLeft": "5",
    "TopRight": "6",
    "BottomRight": "7",
    "BottomLeft": "8",
}

Z1_BORE_SUBCODE = "9"  # inside pocket, both axes
Z1_BOSS_SUBCODE = "10"  # outside pocket, both axes


class Z1UnsupportedOperation(Exception):
    """Raised when an operation has no M480 equivalent on the Z1."""


def z1_probing_active() -> bool:
    """True when the connected machine probes with M480 rather than M460-M469."""
    return CNC.probe_3d_tool == Z1_PROBE_3D_TOOL_NUMBER


def _magnitude(raw) -> str:
    """
    M480 takes distances as magnitudes; the subcode supplies the sign.

    A value the user typed with a leading minus would otherwise cancel the
    subcode's own negation and probe the opposite quadrant.
    """
    text = str(raw).strip()
    if not text:
        return ""
    try:
        return f"{abs(float(text)):g}"
    except (TypeError, ValueError):
        return ""


def generate_m480(subcode: str, config: dict) -> str:
    """Build an ``M480.<subcode>`` line from a probing-screen config."""
    parts = []
    for letter in Z1_SUPPORTED_PARAMS:
        source = Z1_PARAM_SOURCES[letter]
        raw = config.get(source, "")
        value = _magnitude(raw) if letter in ("X", "Y") else str(raw).strip()
        if value:
            parts.append(f"{letter}{value}")
    return f"M480.{subcode}" + ("" if not parts else " " + " ".join(parts))


def ignored_parameters(config: dict) -> list[str]:
    """
    Parameter codes the user set that M480 has no equivalent for.

    Returned so the preview can name them rather than let the operation appear
    to honour settings it cannot.
    """
    mapped = set(Z1_PARAM_SOURCES.values())
    return sorted(code for code, value in config.items() if code not in mapped and str(value).strip())

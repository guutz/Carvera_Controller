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

# Probing-screen parameter codes each kind of generated output actually honours.
# Anything else the user set is reported rather than dropped in silence.
Z1_M480_PARAMS = frozenset({"D", "X", "Y", "E"})
# The single-axis script is generated here, so F (feed), R (retract) and S
# (whether to write the offset) are honoured. L (repeat/average), I (normally
# closed) and Q (angle) are not: they are firmware features of M466.
Z1_SINGLE_AXIS_PARAMS = frozenset({"X", "Y", "Z", "D", "F", "R", "S"})

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

# Single-axis probing has no Z1 macro, so it is generated as a short script of
# the primitives the Z1 does have. These match the machine's own idiom in
# fill_OutCorner_scripts: tap, retract, re-tap at half rate, set the offset.
#
# G38.2 takes a *delta* (ZProbe::probe_XYZ builds `float delta[3]`), so the
# distance is unaffected by G90/G91, and its F is mm/min (divided by 60 on
# arrival). The G91 used for the retract is handed back to G90 afterwards so a
# later command is not silently relative.
#
# Defaults below are the Z1 firmware's own config defaults, used only when the
# machine has not reported a value.
Z1_DEFAULT_PROBE_RATE_MM_M = 100.0  # atc.probe.slow_rate_mm_m
Z1_DEFAULT_RETRACT_MM = 1.0  # atc.probe.retract_mm


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


def ignored_parameters(config: dict, supported=Z1_M480_PARAMS) -> list[str]:
    """
    Parameter codes the user set that the generated command cannot honour.

    Returned so the preview can name them rather than let the operation appear
    to respect settings it does not.
    """
    return sorted(code for code, value in config.items() if code not in supported and str(value).strip())


def _machine_number(key: str, fallback: float) -> float:
    """A number from the machine's own config, or *fallback* if unavailable."""
    from carveracontroller.addons.probing.operations.ConfigUtils import get_machine_config_hint

    try:
        return float(get_machine_config_hint(key))
    except (TypeError, ValueError):
        return fallback


def _setting(config: dict, code: str, fallback: float) -> float:
    try:
        return float(str(config.get(code, "")).strip())
    except (TypeError, ValueError):
        return fallback


def probe_tip_diameter_required(axis: str, config: dict) -> bool:
    """
    True when a tip diameter is needed but absent.

    The Community firmware reads zprobe.probe_tip_diameter from machine config
    and compensates internally. That key does not exist on a Z1, and the
    compensation here is generated host-side, so an X/Y probe without a
    diameter would put the work offset out by the ball radius. Z is exempt:
    tool length is already set from the same ball touching the pad, so the
    trigger point is the surface.
    """
    if axis == "Z":
        return False
    return _setting(config, "D", 0.0) <= 0.0


def generate_single_axis(axis: str, negative: bool, config: dict) -> str:
    """
    Build a double-tap probe along one axis, ending with the work offset set.

    *negative* is the direction of travel, matching the operation's own flag --
    "Left side (X+)" probes toward +X and so finds the face at the lower X.
    """
    distance = abs(_setting(config, axis, 0.0))
    travel = -distance if negative else distance
    direction = -1.0 if negative else 1.0

    rate = _setting(config, "F", 0.0) or _machine_number("atc.probe.slow_rate_mm_m", Z1_DEFAULT_PROBE_RATE_MM_M)
    retract = _setting(config, "R", 0.0) or _machine_number("atc.probe.retract_mm", Z1_DEFAULT_RETRACT_MM)
    tip_radius = _setting(config, "D", 0.0) / 2.0

    back_off = f"G91 G0 {axis}{-direction * retract:g}"

    lines = [
        f"G38.2 {axis}{travel:g} F{rate:g}",
        back_off,
        "G90",
        f"G38.2 {axis}{travel:g} F{rate / 2:g}",
    ]

    # S0 measures without writing the offset; G38.2 still reports [PRB:...].
    if str(config.get("S", "")).strip() != "0":
        # The ball stops one radius short of the face, on the near side, so the
        # face sits at -direction * radius in the new work coordinates.
        offset = 0.0 if axis == "Z" else -direction * tip_radius
        lines.append(f"G10 L20 P0 {axis}{offset:g}")

    # Leave the probe clear of the work. ZProbe::read_probe runs a motion guard
    # that stops the motors and halts whenever the probe reads triggered during
    # a move that is not itself a probe, so ending the cycle still touching
    # turns the next jog into a "3D probe crash" alarm. The machine's own
    # cycles retract here too (fill_OutCorner_scripts, after its G10 L20).
    lines.append(back_off)
    lines.append("G90")

    return "\n".join(lines)

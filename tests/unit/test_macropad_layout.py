"""Tests for the data-driven MacroPad key layout (macropad_layout.json)."""

from __future__ import annotations

import json
import os

import pytest

from carveracontroller.addons.pendant import pendant as pendant_module

from .test_macropad_pendant import ACT, GOTO, SET, make_pendant, stroke

MacroPadPendant = pendant_module.MacroPadPendant


def write_layout(tmp_path, layout) -> str:
    path = tmp_path / MacroPadPendant.LAYOUT_FILENAME
    path.write_text(json.dumps(layout) if not isinstance(layout, str) else layout)
    return str(path)


def load_from(pendant, tmp_path, layout):
    """Point the search path at tmp_path only, then load."""
    write_layout(tmp_path, layout)
    pendant._layout_search_paths = lambda: [str(tmp_path / MacroPadPendant.LAYOUT_FILENAME)]
    return pendant._build_targets()


MINIMAL = {
    "jog": {"0": "X"},
    "modifiers": {"11": {"name": "SET", "targets": {"5": "safe_z"}}},
}


# --- the shipped default --------------------------------------------------------------------


def test_shipped_layout_is_valid_json_and_passes_validation():
    path = os.path.join(os.path.dirname(pendant_module.__file__), MacroPadPendant.LAYOUT_FILENAME)
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)

    pendant = make_pendant()
    pendant._validate_layout(raw)  # must not raise


def test_shipped_layout_matches_the_documented_default_keys():
    """The KEY_* constants are documentation of the shipped layout; keep them honest."""
    pendant = make_pendant()

    assert pendant._modifier_names == {GOTO: "GOTO", ACT: "ACT", SET: "SET"}
    assert pendant._jog_keys == {
        MacroPadPendant.KEY_JOG_X: "X",
        MacroPadPendant.KEY_JOG_Y: "Y",
        MacroPadPendant.KEY_JOG_Z: "Z",
    }


def test_shipped_layout_binds_every_documented_action_name():
    """Guards against a rename in _action_specs silently orphaning a binding."""
    pendant = make_pendant()
    known = set(pendant._action_specs())

    path = os.path.join(os.path.dirname(pendant_module.__file__), MacroPadPendant.LAYOUT_FILENAME)
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)

    for entry in raw["modifiers"].values():
        for action in entry["targets"].values():
            name = action["action"] if isinstance(action, dict) else action
            assert name in known


# --- loading a custom layout -----------------------------------------------------------------


def test_custom_layout_replaces_the_defaults(tmp_path):
    pendant = make_pendant()
    load_from(pendant, tmp_path, MINIMAL)

    assert pendant._jog_keys == {0: "X"}
    assert pendant._modifier_names == {11: "SET"}
    assert pendant._modifier_keys == (11,)
    assert pendant._targets[11][5].label == "SAFE Z"


def test_remapped_binding_actually_fires_the_action(tmp_path):
    pendant = make_pendant()
    load_from(pendant, tmp_path, MINIMAL)

    stroke(pendant, 11, 5)

    assert ("safe_z",) in pendant._controller.calls


def test_confirm_defaults_come_from_the_action_not_the_binding(tmp_path):
    """Zeroing must still confirm wherever it is bound."""
    pendant = make_pendant()
    load_from(pendant, tmp_path, {"jog": {}, "modifiers": {"9": {"name": "X", "targets": {"4": "zero_z"}}}})

    assert pendant._targets[9][4].needs_confirm is True


def test_a_binding_may_override_confirm_explicitly(tmp_path):
    pendant = make_pendant()
    load_from(
        pendant,
        tmp_path,
        {
            "jog": {},
            "modifiers": {
                "9": {"name": "X", "targets": {"4": {"action": "zero_z", "confirm": False}}},
            },
        },
    )

    assert pendant._targets[9][4].needs_confirm is False


def test_macro_actions_are_bindable_and_route_to_the_right_macro(tmp_path):
    pendant = make_pendant()
    load_from(pendant, tmp_path, {"jog": {}, "modifiers": {"9": {"name": "M", "targets": {"4": "macro_7"}}}})

    pendant._targets[9][4].action()

    assert ("macro", 7) in pendant._controller.calls


def test_unknown_modifier_name_still_gets_usable_colours(tmp_path):
    pendant = make_pendant()
    load_from(pendant, tmp_path, {"jog": {}, "modifiers": {"9": {"name": "CUSTOM", "targets": {"4": "stop"}}}})

    assert pendant._modifier_color(9) == MacroPadPendant.DEFAULT_MODIFIER_COLOR
    assert pendant._target_color(9) == MacroPadPendant.DEFAULT_TARGET_COLOR


# --- validation ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("layout", "expected_message"),
    [
        ({"modifiers": {"9": {"name": "G", "targets": {"3": "teleport"}}}}, "unknown action"),
        ({"modifiers": {"99": {"name": "G", "targets": {}}}}, "out of range"),
        ({"modifiers": {"nine": {"name": "G", "targets": {}}}}, "not a key number"),
        ({"modifiers": {"9": {"name": "G", "targets": {"77": "stop"}}}}, "out of range"),
        ({"modifiers": {"9": {"targets": {}}}}, "needs a 'name'"),
        ({"modifiers": {"9": {"name": "G", "targets": []}}}, "'targets' as an object"),
        ({"modifiers": {"9": "GOTO"}}, "must be an object"),
        ({"modifiers": []}, "'modifiers' must be an object"),
        ({"jog": {"0": "Q"}}, "unknown axis"),
        ({"jog": []}, "'jog' must be an object"),
        (
            {"modifiers": {"9": {"name": "G", "targets": {}}, "10": {"name": "G", "targets": {}}}},
            "both named",
        ),
        (
            {"jog": {"3": "X"}, "modifiers": {"3": {"name": "G", "targets": {}}}},
            "both a jog key and",
        ),
    ],
)
def test_validation_rejects_bad_layouts_with_a_useful_message(layout, expected_message):
    pendant = make_pendant()

    with pytest.raises(ValueError, match=expected_message):
        pendant._validate_layout(layout)


def test_unknown_action_error_lists_the_valid_names():
    pendant = make_pendant()

    with pytest.raises(ValueError) as excinfo:
        pendant._validate_layout({"modifiers": {"9": {"name": "G", "targets": {"3": "teleport"}}}})

    assert "margin" in str(excinfo.value)
    assert "zero_xy" in str(excinfo.value)


# --- fallback behaviour -----------------------------------------------------------------------


def test_a_broken_layout_falls_back_to_the_shipped_one_whole(tmp_path):
    """
    One bad binding invalidates the file rather than unbinding a single key -- a pendant
    quietly missing a key you expect is worse than one that plainly used the default.
    """
    bad = {
        "jog": {"0": "X"},
        "modifiers": {"9": {"name": "GOTO", "targets": {"3": "margin", "6": "nope"}}},
    }
    bad_path = write_layout(tmp_path, bad)
    shipped = os.path.join(os.path.dirname(pendant_module.__file__), MacroPadPendant.LAYOUT_FILENAME)

    pendant = make_pendant()
    pendant._layout_search_paths = lambda: [bad_path, shipped]
    pendant._build_targets()

    assert pendant._modifier_names == {GOTO: "GOTO", ACT: "ACT", SET: "SET"}
    assert pendant._targets[GOTO][6].label == "M-HOME"


def test_malformed_json_falls_back(tmp_path):
    bad_path = write_layout(tmp_path, "{not json at all")
    shipped = os.path.join(os.path.dirname(pendant_module.__file__), MacroPadPendant.LAYOUT_FILENAME)

    pendant = make_pendant()
    pendant._layout_search_paths = lambda: [bad_path, shipped]
    pendant._build_targets()

    assert pendant._modifier_names == {GOTO: "GOTO", ACT: "ACT", SET: "SET"}


def test_missing_files_are_skipped_not_fatal(tmp_path):
    shipped = os.path.join(os.path.dirname(pendant_module.__file__), MacroPadPendant.LAYOUT_FILENAME)

    pendant = make_pendant()
    pendant._layout_search_paths = lambda: [str(tmp_path / "nope.json"), shipped]
    pendant._build_targets()

    assert pendant._modifier_names == {GOTO: "GOTO", ACT: "ACT", SET: "SET"}


def test_no_usable_layout_anywhere_leaves_the_pendant_inert_not_crashed(tmp_path):
    pendant = make_pendant()
    pendant._layout_search_paths = lambda: [str(tmp_path / "nope.json")]
    pendant._build_targets()

    assert pendant._targets == {}
    assert pendant._modifier_keys == ()
    # Still safe to drive: no chord resolves, and nothing raises.
    stroke(pendant, 9, 6)
    assert pendant._controller.calls == []


def test_user_copy_takes_precedence_over_the_shipped_layout(tmp_path):
    user_path = write_layout(tmp_path, MINIMAL)
    shipped = os.path.join(os.path.dirname(pendant_module.__file__), MacroPadPendant.LAYOUT_FILENAME)

    pendant = make_pendant()
    pendant._layout_search_paths = lambda: [user_path, shipped]
    pendant._build_targets()

    assert pendant._modifier_names == {11: "SET"}


def test_search_paths_prefer_user_dir_then_shipped():
    pendant = make_pendant()
    paths = pendant._layout_search_paths()

    assert paths[-1] == os.path.join(os.path.dirname(pendant_module.__file__), MacroPadPendant.LAYOUT_FILENAME)

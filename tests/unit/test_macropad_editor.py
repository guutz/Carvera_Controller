"""Tests for the MacroPad layout editor's non-widget behaviour."""

from __future__ import annotations

import json
import os

import pytest

from carveracontroller.addons.pendant import pendant as pendant_module

MacroPadLayoutPopup = pendant_module.MacroPadLayoutPopup
SettingMacroPadLayout = pendant_module.SettingMacroPadLayout
MACROPAD_ACTIONS = pendant_module.MACROPAD_ACTIONS
validate = pendant_module.validate_macropad_layout

VALID = {
    "jog": {"0": "X", "1": "Y"},
    "modifiers": {
        "9": {"name": "GOTO", "targets": {"6": "machine_home"}},
        "10": {"name": "ACT", "targets": {"4": "stop"}},
    },
}


def make_popup(layout=None, on_save=None) -> MacroPadLayoutPopup:
    """Build the popup without touching Kivy's widget tree."""
    popup = MacroPadLayoutPopup.__new__(MacroPadLayoutPopup)
    popup._layout = json.loads(json.dumps(layout if layout is not None else VALID))
    popup._on_save = on_save or (lambda _l: None)
    popup._tab = "9"
    popup._key_buttons = {}
    popup._error_label = None
    return popup


class FakeLabel:
    def __init__(self):
        self.text = ""


# --- the shared action vocabulary --------------------------------------------------------


def test_editor_offers_exactly_the_actions_the_driver_can_bind():
    """
    The editor lists MACROPAD_ACTIONS and the driver binds from it, so a rename cannot
    leave the editor offering something that fails to load.
    """
    pendant = _pendant()
    assert set(MACROPAD_ACTIONS) == set(pendant._action_specs())


def test_every_offered_action_resolves_to_a_real_handler():
    pendant = _pendant()
    specs = pendant._action_specs()

    for name in MACROPAD_ACTIONS:
        label, hint, handler, _confirms = specs[name]
        assert callable(handler), name
        assert label and hint


def test_offered_labels_stay_short_enough_for_the_oled():
    for name, (label, hint, _handler, _confirms) in MACROPAD_ACTIONS.items():
        assert len(label) <= 8, f"{name} banner label too long for the display"
        assert len(hint) <= 6, f"{name} hint label too long for the legend"


def _pendant():
    from .test_macropad_pendant import make_pendant

    return make_pendant()


# --- jog cycling ---------------------------------------------------------------------------


def test_tapping_a_jog_key_cycles_through_the_axes_and_back_to_unbound():
    popup = make_popup()
    popup._tab = MacroPadLayoutPopup.JOG_TAB
    popup._refresh = lambda: None

    seen = []
    for _ in range(5):
        popup._cycle_jog(5)
        seen.append(popup._layout["jog"].get("5"))

    assert seen == ["X", "Y", "Z", "A", None]


def test_cycling_an_already_bound_jog_key_advances_from_its_current_axis():
    popup = make_popup()
    popup._tab = MacroPadLayoutPopup.JOG_TAB
    popup._refresh = lambda: None

    popup._cycle_jog(0)  # was "X"

    assert popup._layout["jog"]["0"] == "Y"


# --- renaming ------------------------------------------------------------------------------


def test_renaming_a_modifier_updates_the_layout():
    popup = make_popup()
    popup._rebuild_tabs = lambda: None

    popup._rename("TRAVEL")

    assert popup._layout["modifiers"]["9"]["name"] == "TRAVEL"


# --- saving --------------------------------------------------------------------------------


def test_save_passes_the_edited_layout_through():
    saved = []
    popup = make_popup(on_save=saved.append)
    popup.dismiss = lambda: None
    popup._layout["modifiers"]["9"]["targets"]["7"] = "safe_z"

    popup._save()

    assert saved and saved[0]["modifiers"]["9"]["targets"]["7"] == "safe_z"


def test_save_refuses_an_invalid_layout_and_explains_why():
    saved = []
    popup = make_popup(on_save=saved.append)
    popup._error_label = FakeLabel()
    popup.dismiss = lambda: pytest.fail("must not close on an invalid layout")
    popup._layout["modifiers"]["9"]["targets"]["6"] = "teleport"

    popup._save()

    assert saved == []
    assert "unknown action" in popup._error_label.text


def test_save_refuses_a_key_that_is_both_a_jog_key_and_a_modifier():
    saved = []
    popup = make_popup(on_save=saved.append)
    popup._error_label = FakeLabel()
    popup.dismiss = lambda: None
    popup._layout["jog"]["9"] = "Z"  # 9 is the GOTO modifier

    popup._save()

    assert saved == []
    assert "both a jog key" in popup._error_label.text


def test_save_refuses_two_modifiers_with_the_same_name():
    saved = []
    popup = make_popup(on_save=saved.append)
    popup._error_label = FakeLabel()
    popup.dismiss = lambda: None
    popup._layout["modifiers"]["10"]["name"] = "GOTO"

    popup._save()

    assert saved == []
    assert "both named" in popup._error_label.text


def test_reset_signals_a_return_to_the_default():
    saved = []
    popup = make_popup(on_save=saved.append)
    popup.dismiss = lambda: None

    popup._reset()

    assert saved == [None]  # None means "delete my copy"


def test_editing_works_on_a_copy_so_cancel_costs_nothing():
    original = json.loads(json.dumps(VALID))
    popup = make_popup(layout=original)
    popup._refresh = lambda: None
    popup._tab = MacroPadLayoutPopup.JOG_TAB

    popup._cycle_jog(0)

    assert original["jog"]["0"] == "X"  # untouched


def test_anything_the_editor_saves_is_loadable_by_the_driver(tmp_path):
    """End to end: save from the editor, then load it the way the pendant does."""
    saved = []
    popup = make_popup(on_save=saved.append)
    popup.dismiss = lambda: None
    popup._layout["modifiers"]["10"]["targets"]["5"] = "probe_laser"
    popup._save()

    path = tmp_path / "macropad_layout.json"
    path.write_text(json.dumps(saved[0]))

    pendant = _pendant()
    pendant._layout_search_paths = lambda: [str(path)]
    pendant._build_targets()

    assert pendant._targets[10][5].action_name == "probe_laser"
    assert pendant._chords[frozenset((10, 5))].label == "LASER"


# --- file handling on the settings row -------------------------------------------------------


def make_setting(tmp_path) -> SettingMacroPadLayout:
    row = SettingMacroPadLayout.__new__(SettingMacroPadLayout)
    row.summary_label = FakeLabel()
    row.section = "carvera"
    row.key = "macropad_layout"
    row.panel = type("P", (), {"set_value": lambda self, s, k, v: None})()
    row._user_layout_path = staticmethod(lambda: str(tmp_path / "macropad_layout.json"))
    row._reload_live_pendant = staticmethod(lambda: None)
    return row


def test_summary_reports_default_until_a_copy_exists(tmp_path):
    row = make_setting(tmp_path)

    assert row._get_summary() == "Default"

    row._save_layout(VALID)
    assert row._get_summary() == "Custom"


def test_saving_writes_readable_json_to_the_user_config_dir(tmp_path):
    row = make_setting(tmp_path)

    row._save_layout(VALID)

    written = json.loads((tmp_path / "macropad_layout.json").read_text())
    assert written == VALID
    validate(written)


def test_reset_removes_the_user_copy(tmp_path):
    row = make_setting(tmp_path)
    row._save_layout(VALID)
    assert os.path.exists(tmp_path / "macropad_layout.json")

    row._save_layout(None)

    assert not os.path.exists(tmp_path / "macropad_layout.json")
    assert row._get_summary() == "Default"


def test_reset_when_no_copy_exists_is_harmless(tmp_path):
    row = make_setting(tmp_path)

    row._save_layout(None)  # must not raise

    assert row._get_summary() == "Default"


def test_loading_falls_back_to_the_shipped_layout_when_there_is_no_copy(tmp_path):
    row = make_setting(tmp_path)

    current = row._load_current()

    assert {entry["name"] for entry in current["modifiers"].values()} == {"GOTO", "ACT", "SET"}


def test_loading_prefers_the_user_copy(tmp_path):
    row = make_setting(tmp_path)
    row._save_layout(VALID)

    current = row._load_current()

    assert {entry["name"] for entry in current["modifiers"].values()} == {"GOTO", "ACT"}


def test_an_unreadable_user_copy_falls_back_instead_of_raising(tmp_path):
    row = make_setting(tmp_path)
    (tmp_path / "macropad_layout.json").write_text("{ not json")

    current = row._load_current()

    assert "modifiers" in current
    assert {entry["name"] for entry in current["modifiers"].values()} == {"GOTO", "ACT", "SET"}

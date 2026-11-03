"""Tests for PluginSet.invalidate().

Both load-order-derived indexes must be dropped together. The bug this
guards against: code that adds an in-memory plugin to a PluginSet and
clears only `_override_index` leaves `_game_index` warm. The new plugin
then has no load-order slot, so `absolute_form_id()` returns None for
it — and every FormID that plugin defines itself becomes unresolvable,
while lookups against other plugins keep working normally.
"""

import pytest

from esplib import LoadOrder, Plugin, PluginSet


@pytest.fixture
def empty_set(tmp_path):
    """A PluginSet with no plugins and an empty load order."""
    lo = LoadOrder.from_list([], data_dir=str(tmp_path), game_id="tes5")
    return PluginSet(lo)


def _inject(ps: PluginSet, plugin: Plugin) -> str:
    """Add an in-memory plugin to a PluginSet, the way callers do."""
    name = plugin.file_path.name
    ps.load_order.plugins.append(name)
    ps._plugins[name] = plugin
    ps._loaded_full[name] = True
    plugin.plugin_set = ps
    return name


def test_invalidate_clears_both_indexes(empty_set, tmp_path):
    p = Plugin.new_plugin(tmp_path / "A.esp")
    _inject(empty_set, p)

    # Warm both caches.
    empty_set.absolute_form_id("A.esp", 0x800)
    empty_set.get_override_chain(0x00000800)
    assert empty_set._game_index is not None
    assert empty_set._override_index is not None

    empty_set.invalidate()

    assert empty_set._game_index is None
    assert empty_set._override_index is None


def test_injected_plugin_addressable_after_invalidate(empty_set, tmp_path):
    """The regression: a plugin added while the game index was warm must
    become addressable once invalidate() runs."""
    first = Plugin.new_plugin(tmp_path / "First.esp")
    _inject(empty_set, first)

    # Warm _game_index while only First.esp is present.
    assert empty_set.absolute_form_id("First.esp", 0x800) is not None

    second = Plugin.new_plugin(tmp_path / "Second.esp")
    _inject(empty_set, second)

    # Stale index: Second.esp has no slot yet.
    assert empty_set.absolute_form_id("Second.esp", 0x800) is None

    empty_set.invalidate()

    assert empty_set.absolute_form_id("Second.esp", 0x800) is not None


def test_self_referencing_formid_resolves_after_invalidate(
        empty_set, tmp_path):
    """End-to-end shape of the furrifier bug: a record a plugin defines
    itself, referenced by its own local FormID, must resolve."""
    other = Plugin.new_plugin(tmp_path / "Other.esp")
    _inject(empty_set, other)
    empty_set.absolute_form_id("Other.esp", 0x800)  # warm the index

    patch = Plugin.new_plugin(tmp_path / "Patch.esp")
    rec = patch.new_record("RACE", edid="SelfDefinedRace")
    patch.add_record(rec)
    _inject(empty_set, patch)

    # Without invalidate() the patch has no slot and this returns None.
    empty_set.invalidate()

    resolved = empty_set.resolve_form_id(rec.form_id, patch)
    assert resolved is not None, \
        "plugin's own record did not resolve through its local FormID"
    assert resolved.editor_id == "SelfDefinedRace"


def test_load_plugin_invalidates(empty_set, tmp_path):
    """load_plugin() routes through invalidate(); a failed load leaves
    the caches alone (nothing was added)."""
    p = Plugin.new_plugin(tmp_path / "A.esp")
    _inject(empty_set, p)
    empty_set.absolute_form_id("A.esp", 0x800)
    assert empty_set._game_index is not None

    # Nonexistent file: load fails, caches stay warm.
    assert empty_set.load_plugin("DoesNotExist.esp") is None
    assert empty_set._game_index is not None

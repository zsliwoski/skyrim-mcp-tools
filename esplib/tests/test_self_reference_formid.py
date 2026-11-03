"""A plugin referencing its own records must stay local.

denormalize_form_id used to fall through to its lazy-add branch for a
self-reference. add_master correctly refuses the self-add, but the
caller took `len(masters) - 1` regardless — the last *existing* master —
so the reference silently pointed at an unrelated plugin.

This only bites after a save: saving stamps local FormIDs from the
sentinel to a concrete self index, so the "is this local?" sentinel
check upstream stops matching and the value reaches this path.
"""

import pytest

from esplib import LoadOrder, Plugin, PluginSet
from esplib.utils import AbsoluteFormID


@pytest.fixture
def injected_patch(tmp_path):
    """A patch with several masters, injected into a PluginSet the way
    the furrifier injects its freshly-written patch."""
    lo = LoadOrder.from_list([], data_dir=str(tmp_path), game_id="tes5")
    ps = PluginSet(lo)

    masters = []
    for name in ("Alpha.esm", "Beta.esm", "Gamma.esm"):
        m = Plugin.new_plugin(tmp_path / name)
        m.plugin_set = ps
        ps.load_order.plugins.append(name)
        ps._plugins[name] = m
        ps._loaded_full[name] = True
        masters.append(name)

    patch = Plugin.new_plugin(tmp_path / "Patch.esp")
    patch.plugin_set = ps
    for name in masters:
        patch.add_master(name)
    ps.load_order.plugins.append("Patch.esp")
    ps._plugins["Patch.esp"] = patch
    ps._loaded_full["Patch.esp"] = True
    ps.invalidate()
    return ps, patch


def test_self_reference_returns_the_local_sentinel(injected_patch):
    ps, patch = injected_patch
    own = ps.absolute_form_id("Patch.esp", 0x000808)
    assert own is not None

    raw = patch.denormalize_form_id(own)

    assert (raw >> 24) == patch._LOCAL_SENTINEL, (
        f"self-reference denormalized to file index 0x{raw >> 24:02X}; "
        f"expected the local sentinel"
    )
    assert (raw & 0xFFFFFF) == 0x000808


def test_self_reference_does_not_alias_a_master(injected_patch):
    """The specific failure: it came back as the last existing master."""
    ps, patch = injected_patch
    before = list(patch.header.masters)

    raw = patch.denormalize_form_id(
        ps.absolute_form_id("Patch.esp", 0x000808))

    file_idx = raw >> 24
    assert file_idx != len(before) - 1, "aliased the last master"
    assert patch.header.masters == before, "must not gain a master"


def test_self_reference_is_registered_for_save_fixup(injected_patch):
    """write_form_id must queue the sentinel for save-time stamping,
    otherwise the sentinel would be written to disk verbatim."""
    ps, patch = injected_patch
    rec = patch.new_record("NPC_", edid="Someone")
    patch.add_record(rec)
    sr = rec.add_subrecord("RNAM", b"\x00\x00\x00\x00")

    patch.write_form_id(sr, 0, ps.absolute_form_id("Patch.esp", 0x000808))

    assert (sr.get_uint32() >> 24) == patch._LOCAL_SENTINEL
    assert any(entry[0] is sr for entry in patch._local_formid_fixups), \
        "self-reference was not queued for save-time fixup"


def test_real_master_reference_still_resolves(injected_patch):
    ps, patch = injected_patch
    raw = patch.denormalize_form_id(
        ps.absolute_form_id("Beta.esm", 0x001234))
    assert patch.header.masters[raw >> 24].lower() == "beta.esm"
    assert (raw & 0xFFFFFF) == 0x001234


# ---------------------------------------------------------------------
# The same hazard on the NORMALIZE side, for a patch that is not (yet)
# in the load order.
#
# A file index of len(masters) means "one of my own records".
# normalize_form_id resolves that by looking the plugin's own filename up
# in the load order -- which fails for a patch still being built, before
# it is injected. It used to hand back the raw value, and the raw self
# index then read as a LOAD-ORDER slot downstream, resolving to whatever
# unrelated plugin happened to sit there.
#
# Only reachable after a save, for the reason in the module docstring:
# saving stamps the sentinel with a concrete self index, so the sentinel
# check above stops matching and the value reaches this path.
# ---------------------------------------------------------------------


@pytest.fixture
def uninjected_patch(tmp_path):
    """A patch whose masters are in the load order but which is NOT
    itself listed -- and where the load-order slot equal to the patch's
    self index is occupied by an unrelated plugin, so a leaked raw value
    resolves to something real and wrong."""
    lo = LoadOrder.from_list([], data_dir=str(tmp_path), game_id="tes5")
    ps = PluginSet(lo)

    masters = ["Alpha.esm", "Beta.esm", "Gamma.esm"]
    # Delta lands at load-order slot 3 == the patch's self index.
    for name in masters + ["Delta.esm"]:
        m = Plugin.new_plugin(tmp_path / name)
        m.plugin_set = ps
        ps.load_order.plugins.append(name)
        ps._plugins[name] = m
        ps._loaded_full[name] = True

    patch = Plugin.new_plugin(tmp_path / "Patch.esp")
    patch.plugin_set = ps
    for name in masters:
        patch.add_master(name)
    ps.invalidate()
    return ps, patch


def _self_ref(patch, obj_id=0x000808):
    """A FormID as it looks after save-time stamping: the patch's own
    self index in the high byte instead of the sentinel."""
    return (len(patch.header.masters) << 24) | obj_id


def test_uninjected_self_reference_normalizes_to_the_sentinel(
        uninjected_patch):
    _ps, patch = uninjected_patch
    norm = patch.normalize_form_id(_self_ref(patch))
    assert (norm.value >> 24) == patch._LOCAL_SENTINEL, (
        f"self-reference normalized to 0x{norm.value:08X}; expected the "
        f"local sentinel")
    assert (norm.value & 0xFFFFFF) == 0x000808


def test_uninjected_self_reference_does_not_become_another_plugin(
        uninjected_patch):
    """The concrete failure: the patch's own record came back as the
    plugin occupying that load-order slot."""
    ps, patch = uninjected_patch
    delta = ps.absolute_form_id("Delta.esm", 0x000808)
    norm = patch.normalize_form_id(_self_ref(patch))
    assert norm.value != delta.value, \
        "own record aliased Delta.esm, the plugin at that load-order slot"


def test_uninjected_self_reference_round_trips(uninjected_patch):
    """normalize -> write must land back on the sentinel and be queued
    for save-time stamping, not silently point elsewhere."""
    _ps, patch = uninjected_patch
    rec = patch.new_record("NPC_", edid="Someone")
    patch.add_record(rec)
    sr = rec.add_subrecord("RNAM", b"\x00\x00\x00\x00")

    patch.write_form_id(sr, 0, patch.normalize_form_id(_self_ref(patch)))

    assert (sr.get_uint32() >> 24) == patch._LOCAL_SENTINEL
    assert (sr.get_uint32() & 0xFFFFFF) == 0x000808
    assert any(entry[0] is sr for entry in patch._local_formid_fixups)


def test_uninjected_master_reference_is_unaffected(uninjected_patch):
    """Only the self-index case changes; a real master still resolves,
    and a genuinely unresolvable master still falls through."""
    ps, patch = uninjected_patch
    norm = patch.normalize_form_id(0x01001234)   # master index 1 = Beta
    assert norm.value == ps.absolute_form_id("Beta.esm", 0x001234).value


def test_injected_self_reference_still_uses_the_load_order(injected_patch):
    """When the patch IS in the load order there is a real absolute
    FormID for its own records, and that must still win over the
    sentinel shortcut."""
    ps, patch = injected_patch
    norm = patch.normalize_form_id(_self_ref(patch))
    assert norm.value == ps.absolute_form_id("Patch.esp", 0x000808).value
    assert (norm.value >> 24) != patch._LOCAL_SENTINEL

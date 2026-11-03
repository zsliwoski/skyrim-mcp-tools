"""The lazily-built override index must never be visible half-built.

PluginSet has no locking, and callers share one instance across threads
(the furrifier's GUI previews an NPC on one QThread while a Run bakes on
another). The index used to be published empty and filled in place, so a
reader arriving mid-build saw a non-None index missing most of its
entries and got None for FormIDs that do exist — a silent wrong answer,
no exception.
"""

import threading

import pytest

from esplib import LoadOrder, Plugin, PluginSet


@pytest.fixture
def populated_set(tmp_path):
    """A PluginSet with enough records that building the index is not
    instantaneous — the race needs a window to land in."""
    lo = LoadOrder.from_list([], data_dir=str(tmp_path), game_id="tes5")
    ps = PluginSet(lo)

    plugin = Plugin.new_plugin(tmp_path / "Many.esp")
    plugin.plugin_set = ps
    for i in range(2000):
        rec = plugin.new_record("RACE", edid=f"Race{i:04d}")
        plugin.add_record(rec)

    name = plugin.file_path.name
    ps.load_order.plugins.append(name)
    ps._plugins[name] = plugin
    ps._loaded_full[name] = True
    ps.invalidate()
    return ps, plugin


def test_index_is_published_complete(populated_set):
    """Directly: after a build, every record resolves."""
    ps, plugin = populated_set
    ps._build_override_index()
    assert len(ps._override_index) == 2000
    for rec in plugin.records:
        assert ps.resolve_form_id(rec.form_id, plugin) is not None


def test_concurrent_readers_never_see_a_partial_index(populated_set):
    """A reader that starts mid-build must not get None for a record
    that exists. Fails against a publish-then-fill implementation."""
    ps, plugin = populated_set
    targets = [plugin.records[0], plugin.records[999], plugin.records[-1]]
    misses: list = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            for rec in targets:
                # Only meaningful once someone has published an index.
                if ps._override_index is None:
                    continue
                if ps.resolve_form_id(rec.form_id, plugin) is None:
                    misses.append(rec.editor_id)

    def builder():
        for _ in range(40):
            ps.invalidate()
            ps.get_override_chain(0)   # forces a rebuild

    readers = [threading.Thread(target=reader) for _ in range(3)]
    for t in readers:
        t.start()
    try:
        builder()
    finally:
        stop.set()
        for t in readers:
            t.join(timeout=10)

    assert not misses, (
        f"{len(misses)} lookup(s) missed a record that exists — the index "
        f"was readable while still being built (e.g. {misses[:5]})"
    )

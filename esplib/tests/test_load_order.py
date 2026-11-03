"""LoadOrder tests.

Currently focused on plugin resolution under a virtual filesystem —
see TestReadabilityNotStat for why that needs its own coverage.
"""


class TestReadabilityNotStat:
    """Existence must be decided by opening, not by stat.

    Under Mod Organizer's virtual filesystem the two disagree: a
    mod-supplied plugin enumerates and opens fine while stat reports it
    absent, so every mod in an MO2 load order looked missing to us. We
    can't run usvfs in a test, so we simulate the split directly —
    patch stat to deny the file and assert we find it anyway.
    """

    def test_plugin_path_finds_a_file_stat_denies(self, tmp_path,
                                                  monkeypatch):
        from esplib.load_order import LoadOrder

        (tmp_path / "FurryMod.esp").write_bytes(b"TES4")
        lo = LoadOrder.from_list(["FurryMod.esp"], data_dir=tmp_path)
        assert lo.plugin_path("FurryMod.esp") is not None

        # Now make stat lie the way usvfs does, leaving open() working.
        import os
        real_stat = os.stat

        def blind_stat(path, *a, **k):
            if "FurryMod" in str(path):
                raise FileNotFoundError(2, "No such file or directory")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(os, "stat", blind_stat)
        assert not (tmp_path / "FurryMod.esp").exists(), \
            "precondition: stat must be blind for this test to mean anything"
        assert lo.plugin_path("FurryMod.esp") is not None, \
            "plugin_path must open the file, not stat it"

    def test_plugin_path_still_returns_none_for_a_real_absence(self,
                                                               tmp_path):
        from esplib.load_order import LoadOrder
        lo = LoadOrder.from_list(["Nope.esp"], data_dir=tmp_path)
        assert lo.plugin_path("Nope.esp") is None

    def test_plugin_path_rejects_a_directory(self, tmp_path):
        """A directory named like a plugin isn't a plugin."""
        from esplib.load_order import LoadOrder
        (tmp_path / "Weird.esp").mkdir()
        lo = LoadOrder.from_list(["Weird.esp"], data_dir=tmp_path)
        assert lo.plugin_path("Weird.esp") is None

"""Plugin packaging."""
import zipfile

import pytest

import server

pytestmark = pytest.mark.game


class TestPackage:
    def test_package_includes_seq_for_start_game_quests(self, npc_plugin):
        pkg = server.package_plugin("TestMod.esp")
        with zipfile.ZipFile(pkg["zip"]) as z:
            seq = z.read("SEQ/TestMod.seq")
        assert len(seq) % 4 == 0 and len(seq) >= 4

    def test_package_missing_plugin(self, sandbox):
        assert "error" in server.package_plugin("Missing.esp")


class TestMultiplePlugins:
    @pytest.fixture
    def two(self, sandbox):
        from esplib import Plugin
        for name in ("One.esp", "Two.esp"):
            p = Plugin.new_plugin(str(sandbox / name), masters=["Skyrim.esm"], game="tes5")
            p.new_record("MISC", name[:3] + "Item", form_id=0x800)
            p.save(str(sandbox / name))

    def test_package_several(self, two, sandbox):
        res = server.package_plugin(["One.esp", "Two.esp", "Missing.esp"])
        assert set(res) == {"One.esp", "Two.esp", "Missing.esp"}
        assert "error" in res["Missing.esp"]
        for name in ("One", "Two"):
            with zipfile.ZipFile(res[name + ".esp"]["zip"]) as z:
                assert f"{name}.esp" in z.namelist()

    def test_package_list_rejects_zip_path(self, two, sandbox):
        assert "error" in server.package_plugin(["One.esp"], zip_path=str(sandbox / "x.zip"))

    def test_package_single_unchanged(self, two):
        assert "zip" in server.package_plugin("One.esp")

    def test_install_several_dry_run(self, two):
        res = server.install_plugin(["One.esp", "Missing.esp"], dry_run=True)
        assert "error" not in res["One.esp"] and "error" in res["Missing.esp"]
        assert server.install_plugin([]) == {"error": "give at least one plugin"}


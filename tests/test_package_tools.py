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

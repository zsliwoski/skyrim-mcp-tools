"""Generic record editing and creation."""

import pytest

import server
from helpers import sigs

pytestmark = pytest.mark.game


class TestRecords:
    def test_edit_record_makes_patch(self, sandbox):
        res = server.edit_record("Firewood01", "RecPatch.esp", edits=[{"sig": "FULL", "string": "Kindling"}])
        assert "error" not in res, res
        assert server.get_record("Firewood01", plugin="RecPatch.esp")["name"] == "Kindling"

    def test_edit_record_unknown(self, sandbox):
        assert "error" in server.edit_record("ZzzNoSuchRecord", "RecPatch.esp", edits=[])

    def test_create_record(self, sandbox):
        res = server.create_record("Rec.esp", "MISC", "TestMisc", [
            {"sig": "FULL", "text": "Test Misc"}, {"sig": "DATA", "hex": "0a00000000000000"}])
        assert "error" not in res, res
        assert sigs(sandbox / "Rec.esp") == {"MISC": ["TestMisc"]}

    def test_create_record_duplicate_editor_id(self, sandbox):
        args = ("Rec.esp", "MISC", "TestMisc", [{"sig": "FULL", "text": "x"}])
        server.create_record(*args)
        assert "error" in server.create_record(*args)

    def test_create_enchantment(self, sandbox):
        res = server.create_enchantment("Rec.esp", "TestEnch", "Test Fire", [
            {"effect": "EnchFireDamageFFContact", "magnitude": 7}], cost=300)
        assert "error" not in res, res
        assert sigs(sandbox / "Rec.esp") == {"ENCH": ["TestEnch"]}

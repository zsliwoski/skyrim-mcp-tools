"""Read-only tools against the real Skyrim.esm (loaded once per run, then cached)."""
import pytest

import server

pytestmark = pytest.mark.game


class TestSearch:
    def test_search_npcs_by_name(self):
        hits = server.search_npcs("Lydia")
        assert [h["editor_id"] for h in hits] == ["HousecarlWhiterun"]
        assert hits[0]["plugin"] == "Skyrim.esm"

    def test_search_npcs_by_form_id(self):
        assert server.search_npcs("0001347F")[0]["editor_id"] == "Sven"

    def test_search_npcs_limit(self):
        assert len(server.search_npcs("a", limit=3)) == 3

    def test_search_quests(self):
        (hit,) = server.search_quests("MQ101")
        assert hit["name"] == "Unbound" and hit["quest_type"] == "Main Quest"

    def test_search_forms_by_signature(self):
        hits = server.search_forms("race", "Nord")
        assert {"NordRace"} <= {h["editor_id"] for h in hits}
        assert all(h["type"] == "RACE" for h in hits)

    def test_search_cells_flags_interiors(self):
        cells = server.search_cells("Riverwood")
        assert any(c["interior"] for c in cells)

    def test_search_references_sorts_by_distance(self):
        refs = server.search_references("WoodChoppingBlock", worldspace="Tamriel", x=18900, y=-43500,
                                        radius=3000)
        assert refs[0]["ref"] == "0B555A" and refs[0]["cell"] == "Riverwood01"
        dist = [(r["x"] - 18900) ** 2 + (r["y"] + 43500) ** 2 for r in refs]
        assert dist == sorted(dist)

    def test_search_references_unknown_base_reports_error(self):
        (res,) = server.search_references("ZzzNoSuchBase")
        assert "error" in res


class TestGetNpc:
    def test_get_npc(self):
        npc = server.get_npc("Lydia")
        assert npc["editor_id"] == "HousecarlWhiterun"
        assert {"stats", "race", "factions"} <= set(npc)

    def test_unknown_npc(self):
        assert "error" in server.get_npc("ZzzNoSuchNpc")

    def test_get_npc_face(self):
        face = server.get_npc_face("Sven")
        assert face["race"] == "NordRace"
        assert -1 <= face["face"]["nose_length"] <= 1
        assert face["slider_meanings"]

    def test_get_npc_dialogue_counts_match_lists(self):
        res = server.get_npc_dialogue("Sven", limit=3)
        assert res["npc"]["editor_id"] == "Sven"
        assert res["counts"]["direct"] > 3
        assert len(res["direct"]) == 3

    def test_get_npc_dialogue_without_generic(self):
        res = server.get_npc_dialogue("Sven", include_generic=False, limit=1)
        assert res["counts"].get("generic", 0) == 0


class TestQuests:
    def test_get_quest(self):
        q = server.get_quest("MQ101")
        assert q["editor_id"] == "MQ101" and q["stages"] and q["aliases"]

    def test_get_quest_structure(self):
        s = server.get_quest_structure("MQ101")
        assert s["fragment_script"] == "QF_MQ101_0003372B"
        assert s["objectives"] and s["stage_transitions"] is not None


class TestScriptsAndRecords:
    def test_get_script_reads_bsa(self):
        s = server.get_script("DialogueFollowerScript")
        assert s["objects"][0]["extends"] == "Quest"
        assert "source" not in s or s["source"].endswith(".psc")

    def test_get_script_with_source(self):
        s = server.get_script("DialogueFollowerScript", include_source=True)
        assert "SetFollower" in str(s)

    def test_get_record(self):
        r = server.get_record("Firewood01")
        assert r["signature"] == "MISC" and r["form_id"] == "0006F993"
        assert r["subrecords"][0]["sig"] == "EDID"

    def test_get_record_unknown(self):
        assert "error" in server.get_record("ZzzNoSuchRecord")

    def test_describe_npc_fields(self):
        fields = server.describe_npc_fields()
        assert {"editor_id", "name", "clone_from", "follower", "dialogue", "placement"} <= set(fields)

    def test_weather_placeholder(self):
        assert "Whiterun" in server.get_weather()

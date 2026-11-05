"""Papyrus API database: .psc parsing, indexing, exact lookup through inheritance, and separation from lore."""
import numpy as np
import pytest

from tools.knowledge import vectors
from tools.lore import db as lore_db
from tools.papyrus_api import api, psc

OBJECT_REF = """ScriptName ObjectReference extends Form Hidden
{A thing in the world.}

Event OnHit(ObjectReference akAggressor, Form akSource, Projectile akProjectile, bool abPowerAttack, \\
  bool abSneakAttack, bool abBashAttack, bool abHitBlocked)
  ; body is skipped
  Function Fake()
  EndFunction
EndEvent

Function Activate(ObjectReference akActivator, bool abDefaultProcessingOnly = false) native
Int Function GetItemCount(Form akItem = None) native
{Counts items.}
Int Property Counter Auto
Bool Property Gate
  Bool Function Get()
    return true
  EndFunction
EndProperty
"""
ACTOR = """ScriptName Actor extends ObjectReference
; comment
Function DamageActorValue(string asValueName, float afDamage) native
Event OnDying(Actor akKiller)
EndEvent
"""
SKSE_ACTOR = ACTOR + "Function SKSEOnlyCall() native\n"
SKSE_ONLY = "ScriptName ModEvent Hidden\nFunction Send(int handle) global native\n"


@pytest.fixture
def con(tmp_path):
    vanilla, skse = tmp_path / "v", tmp_path / "s"
    vanilla.mkdir(), skse.mkdir()
    (vanilla / "ObjectReference.psc").write_text(OBJECT_REF)
    (vanilla / "Actor.psc").write_text(ACTOR)
    (skse / "Actor.psc").write_text(SKSE_ACTOR)
    (skse / "ModEvent.psc").write_text(SKSE_ONLY)
    con = api.connect(tmp_path / "api.db", create=True)
    api.index_scripts(con, [("skyrim", vanilla), ("skse", skse)])
    return con


def test_parse_header_members_and_skipped_bodies():
    s = psc.parse(OBJECT_REF)
    assert (s['name'], s['extends'], s['doc']) == ("ObjectReference", "Form", "A thing in the world.")
    names = [(m['kind'], m['name']) for m in s['members']]
    assert names == [('event', 'OnHit'), ('function', 'Activate'), ('function', 'GetItemCount'),
                     ('property', 'Counter'), ('property', 'Gate')]
    hit = s['members'][0]
    assert "bool abBashAttack" in hit['params'] and hit['kind'] == 'event'
    assert s['members'][2]['doc'] == "Counts items." and s['members'][2]['returns'] == 'Int'
    assert psc.signature(s['members'][1]).endswith("native")


def test_get_script_lists_members_and_chain(con):
    out = api.get_script(con, "actor")
    assert out['script'] == "Actor" and out['extends_chain'] == ["ObjectReference"]
    assert {m['name'] for m in out['members']} == {"DamageActorValue", "OnDying", "SKSEOnlyCall"}


def test_member_lookup_walks_up_parents(con):
    out = api.get_script(con, "Actor", "getitemcount")
    assert out['defined_in'] == "ObjectReference"
    assert out['members'][0]['signature'].startswith("Int Function GetItemCount(")


def test_unknown_member_is_empty_not_fuzzy(con):
    assert api.get_script(con, "Actor", "OnMagicallyHit")['members'] == []
    assert api.get_script(con, "Nope") is None
    assert api.suggest(con, "Damage")[0]['section'] == "DamageActorValue"


def test_skse_only_members_are_flagged(con):
    by = {m['name']: m['skse'] for m in api.get_script(con, "Actor")['members']}
    assert by == {"DamageActorValue": False, "OnDying": False, "SKSEOnlyCall": True}
    assert api.get_script(con, "ModEvent")['skse_only'] is True


def test_search_finds_events_by_name(con):
    hits = api.search(con, "OnHit")
    assert hits and hits[0]['title'] == "ObjectReference" and hits[0]['source'] == "psc"


def test_search_maps_intent_phrases_to_events(con):
    assert api.search(con, "detect when something hits the actor")[0]['section'] == "OnHit"


def test_api_and_lore_databases_are_independent(con, tmp_path):
    lore = lore_db.connect(tmp_path / "lore.db", create=True)
    assert lore_db.search(lore, "OnHit") == []
    assert api.status(con)['path'] != lore_db.status(lore)['path']
    with pytest.raises(FileNotFoundError, match="tools.papyrus_api build"):
        api.connect(tmp_path / "missing.db")


def test_shared_vectors_work_on_the_api_database(con, tmp_path):
    class Emb:
        model = "fake"

        def _v(self, t):
            v = np.zeros(26, dtype=np.float32)
            for ch in t.lower():
                if 'a' <= ch <= 'z':
                    v[ord(ch) - 97] += 1
            return v

        def embed(self, texts):
            return [self._v(t) for t in texts]

        def query(self, t):
            return self._v(t)

    assert vectors.build(con, embedder=Emb()) > 0
    assert api.search(con, "damage actor value", 3, mode="semantic", embedder=Emb())
    empty = api.connect(tmp_path / "empty.db", create=True)
    with pytest.raises(ValueError, match="tools.papyrus_api build vectors"):
        api.search(empty, "x", mode="semantic")


def test_native_only_drops_content_scripts_and_names_become_keywords(tmp_path):
    folder = tmp_path / "v"
    folder.mkdir()
    (folder / "Actor.psc").write_text(ACTOR)
    (folder / "QF_Quest_000123.psc").write_text("ScriptName QF_Quest_000123 extends Quest\nFunction Fragment_1()\nEndFunction\n")
    con = api.connect(tmp_path / "api.db", create=True)
    api.index_scripts(con, [("skyrim", folder)])
    assert api.get_script(con, "QF_Quest_000123") is None
    assert api.search(con, "damage actor value")[0]['section'] == "DamageActorValue"
    assert api.get_script(con, "Actor", "DamageActorValue")['members'][0]['doc'] == ""
    api.index_scripts(con, [("skyrim", folder)], native_only=False)
    assert api.get_script(con, "QF_Quest_000123") is not None

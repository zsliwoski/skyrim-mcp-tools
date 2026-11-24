import pytest

from tools.papyrus import papyrus_log as pl

LOG = """[11/23/2026 - 09:00:00PM] Papyrus log opened
[11/23/2026 - 09:00:05PM] error: Cannot call GetStage() on a None object, aborting function call
stack:
\t[Alias (0001A2B3)].MyQuestScript.OnInit() - "MyQuestScript.psc" Line 12
[11/23/2026 - 09:00:06PM] error: Cannot call GetStage() on a None object, aborting function call
stack:
\t[Alias (0001A2B4)].MyQuestScript.OnInit() - "MyQuestScript.psc" Line 12
[11/23/2026 - 09:00:07PM] warning: Unable to find script OtherScript
[11/23/2026 - 09:00:08PM] info: hello from Other
"""


@pytest.fixture
def log_dir(tmp_path):
    (tmp_path / "Papyrus.0.log").write_text(LOG)
    return tmp_path


def test_groups_repeats_and_hints(log_dir):
    r = pl.read_log(log_dir)
    assert r["counts"] == {"error": 2, "warning": 1, "info": 2}
    top = r["groups"][0]
    assert top["count"] == 2 and top["first"] != top["last"]
    assert "property" in top["hint"].lower()
    assert any("MyQuestScript.psc" in s for s in top["stack"])


def test_filter_and_level(log_dir):
    assert pl.read_log(log_dir, text_filter="MyQuestScript")["counts"]["error"] == 2
    r = pl.read_log(log_dir, level="warning")
    assert r["counts"]["info"] == 0 and r["counts"]["warning"] == 1


def test_missing_log_and_bad_level(log_dir):
    with pytest.raises(FileNotFoundError):
        pl.read_log(log_dir, log=3)
    with pytest.raises(ValueError):
        pl.read_log(log_dir, level="fatal")


def test_empty_log_explains_logging_is_off(tmp_path):
    (tmp_path / "Papyrus.0.log").write_text("")
    assert "bEnableLogging" in pl.read_log(tmp_path)["note"]


def test_find_dir_and_list(log_dir, tmp_path):
    assert pl.find_log_dir([tmp_path / "nope", log_dir]) == log_dir
    assert pl.find_log_dir([tmp_path / "nope"]) is None
    assert pl.list_logs(log_dir)[0]["file"] == "Papyrus.0.log"

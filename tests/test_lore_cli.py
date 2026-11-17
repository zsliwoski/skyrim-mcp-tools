"""Lore command line and game-book indexing, with all I/O stubbed."""
import json
import runpy
import sys
from types import SimpleNamespace

import pytest

from tools.lore import cli, db, game, uesp, vectors


@pytest.fixture
def calls(monkeypatch, tmp_path):
    log = []
    monkeypatch.setattr(db, "connect", lambda *a, **k: log.append(("connect", k)) or "CON")
    monkeypatch.setattr(db, "db_path", lambda: tmp_path / "lore.db")
    monkeypatch.setattr(db, "status", lambda con: {"con": con})
    monkeypatch.setattr(uesp, "import_dump", lambda *a: log.append(("dump", a)) or 3)
    monkeypatch.setattr(uesp, "crawl", lambda *a: log.append(("crawl", a)) or 4)
    monkeypatch.setattr(vectors, "build", lambda con, **k: log.append(("vectors", k)) or 5)
    monkeypatch.setattr(vectors, "search", lambda *a: log.append(("search", a)) or [{"title": "T"}])
    monkeypatch.setattr(cli, "build_game_books", lambda con, plugins: log.append(("books", plugins)) or 6)
    return log


def test_build_dump(calls, capsys):
    cli.main(["build", "uesp-dump", "x.xml", "--namespaces", "Lore"])
    assert calls[1] == ("dump", ("CON", "x.xml", ["Lore"], cli._print))
    assert "indexed 3 chunks" in capsys.readouterr().out


def test_build_crawl_defaults(calls, capsys):
    cli.main(["build", "uesp-crawl", "--limit", "7", "--delay", "0"])
    assert calls[1] == ("crawl", ("CON", list(uesp.DEFAULT_NAMESPACES), 0.0, 7, cli._print))
    assert "indexed 4 chunks" in capsys.readouterr().out


def test_build_vectors_and_books(calls, capsys):
    cli.main(["build", "vectors", "--model", "m", "--batch", "2"])
    assert calls[-1] == ("vectors", {"model": "m", "batch": 2, "progress": cli._print})
    cli.main(["build", "game-books", "--plugins", "A.esm"])
    assert calls[-1] == ("books", ["A.esm"])
    assert "indexed 6 chunks" in capsys.readouterr().out


def test_status_and_search(calls, capsys):
    cli.main(["status"])
    assert json.loads(capsys.readouterr().out) == {"con": "CON"}
    cli.main(["search", "dragon", "--limit", "2", "--source", "game", "--mode", "keyword"])
    assert calls[-1] == ("search", ("CON", "dragon", 2, "game", "keyword"))
    assert json.loads(capsys.readouterr().out) == [{"title": "T"}]


def test_print_goes_to_stderr(capsys):
    cli._print("hi")
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == "hi\n"


def test_build_game_books_uses_server_model(monkeypatch):
    import server
    monkeypatch.setattr(server, "get_model", lambda plugins: ("model", plugins))
    monkeypatch.setattr(game, "index_books", lambda con, model: (con, model))
    assert cli.build_game_books("c", ["P"]) == ("c", ("model", ["P"]))


def test_dunder_main_runs_cli(calls, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tools.lore", "status"])
    runpy.run_module("tools.lore.__main__", run_name="__main__")
    assert json.loads(capsys.readouterr().out) == {"con": "CON"}


def _rec(name, editor_id, form_id, text):
    return SimpleNamespace(plugin="p", full_name=name, editor_id=editor_id,
                           form_id=SimpleNamespace(value=form_id), text=text)


def test_index_books_skips_short_and_names_fallbacks(monkeypatch, tmp_path):
    long_text = " ".join(f"word{i}" for i in range(20))
    recs = {
        1: _rec("Named", "NamedBook", 0x01000001, long_text + "[pagebreak]" + long_text),
        2: _rec(None, "EdidOnly", 0x01000002, long_text),
        3: _rec(None, None, 0x01ABCDEF, long_text),
        4: _rec("Short", "ShortBook", 0x01000004, "too few words"),
        5: _rec("None", "NoText", 0x01000005, None),
    }
    monkeypatch.setattr(game, "book_text", lambda plugin, rec: rec.text)
    con = db.connect(tmp_path / "g.db", create=True)
    n = game.index_books(con, SimpleNamespace(by_sig={"BOOK": recs}))
    titles = {r["title"] for r in con.execute("SELECT title FROM chunks")}
    assert titles == {"Book: Named", "Book: EdidOnly", "Book: ABCDEF"}
    assert n == con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] >= 3
    assert db.status(con)["sources"][0]["name"] == "game"

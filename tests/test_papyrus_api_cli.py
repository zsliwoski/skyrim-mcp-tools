"""Papyrus API command line, with the database and indexers stubbed."""
import json
import runpy
import sys

import pytest

from tools.papyrus_api import api, cli


@pytest.fixture
def calls(monkeypatch, tmp_path):
    log = []
    monkeypatch.setattr(api, "connect", lambda *a, **k: "CON")
    monkeypatch.setattr(api, "db_path", lambda: tmp_path / "api.db")
    monkeypatch.setattr(api, "status", lambda con: {"s": 1})
    monkeypatch.setattr(api, "get_script", lambda *a: log.append(("get", a)) or {"found": True})
    monkeypatch.setattr(api, "search", lambda *a: log.append(("search", a)) or [])
    monkeypatch.setattr(api, "index_scripts", lambda con, dirs, native_only: log.append(("scripts", dirs, native_only)) or 1)
    monkeypatch.setattr(api, "import_ckwiki_dump", lambda *a: log.append(("dump", a)) or 2)
    monkeypatch.setattr(api, "crawl_ckwiki", lambda *a: log.append(("crawl", a)) or 3)
    monkeypatch.setattr(cli.vectors, "build", lambda con, **k: log.append(("vectors", k)) or 4)
    return log


def test_build_scripts_explicit_dirs(calls, capsys):
    cli.main(["build", "scripts", "--source-dir", "skyrim=C:/a=b", "skse=D", "--all-scripts"])
    assert calls == [("scripts", [("skyrim", "C:/a=b"), ("skse", "D")], False)]
    assert "indexed 1 chunks" in capsys.readouterr().out


def test_build_scripts_default_dirs(calls, monkeypatch):
    monkeypatch.setattr(cli, "default_script_dirs", lambda: [("skyrim", "X")])
    cli.main(["build", "scripts"])
    assert calls == [("scripts", [("skyrim", "X")], True)]


def test_build_actor_values(calls, monkeypatch, capsys):
    import server
    monkeypatch.setattr(server, "get_model", lambda plugins: "MODEL")
    monkeypatch.setattr(api, "index_actor_values", lambda con, model: model == "MODEL" and 9)
    cli.main(["build", "actor-values", "--plugins", "Skyrim.esm"])
    assert "indexed 9 chunks" in capsys.readouterr().out


def test_build_ckwiki_and_vectors(calls, capsys):
    cli.main(["build", "ckwiki-dump", "f.xml"])
    assert calls[-1] == ("dump", ("CON", "f.xml", [''], cli._print))
    cli.main(["build", "ckwiki-crawl", "--delay", "0", "--limit", "5", "--namespaces", "A", "B"])
    assert calls[-1] == ("crawl", ("CON", ["A", "B"], 0.0, 5, cli._print))
    cli.main(["build", "vectors", "--batch", "3"])
    assert calls[-1][1]["batch"] == 3 and calls[-1][1]["progress"] is cli._print
    assert capsys.readouterr().out.count("indexed") == 3


def test_status_get_search(calls, capsys):
    cli.main(["status"])
    assert json.loads(capsys.readouterr().out) == {"s": 1}
    cli.main(["get", "Actor", "GetName"])
    assert calls[-1] == ("get", ("CON", "Actor", "GetName"))
    assert json.loads(capsys.readouterr().out) == {"found": True}
    cli.main(["search", "health", "--limit", "3", "--source", "psc", "--mode", "hybrid"])
    assert calls[-1] == ("search", ("CON", "health", 3, "psc", "hybrid"))
    assert json.loads(capsys.readouterr().out) == []


def test_print_goes_to_stderr(capsys):
    cli._print("x")
    assert capsys.readouterr().err == "x\n"


def test_default_script_dirs(monkeypatch):
    from tools.papyrus import papyrus_build as pb
    monkeypatch.setattr(pb, "skse_source_dirs", lambda: ["S1"])
    monkeypatch.setattr(pb, "ensure_sources", lambda: "VANILLA")
    assert cli.default_script_dirs() == [("skyrim", "VANILLA"), ("skse", "S1")]


def test_default_script_dirs_tolerates_missing_vanilla_only_with_skse(monkeypatch):
    from tools.papyrus import papyrus_build as pb

    def boom():
        raise pb.CompileError("no sources")
    monkeypatch.setattr(pb, "ensure_sources", boom)
    monkeypatch.setattr(pb, "skse_source_dirs", lambda: ["S1"])
    assert cli.default_script_dirs() == [("skse", "S1")]
    monkeypatch.setattr(pb, "skse_source_dirs", lambda: [])
    with pytest.raises(pb.CompileError):
        cli.default_script_dirs()


def test_dunder_main_runs_cli(calls, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tools.papyrus_api", "status"])
    runpy.run_module("tools.papyrus_api.__main__", run_name="__main__")
    assert json.loads(capsys.readouterr().out) == {"s": 1}

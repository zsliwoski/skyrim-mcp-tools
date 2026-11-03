"""Helpers that must not depend on stat.

Under Mod Organizer's virtual filesystem, directory enumeration and
file opening see the merged view of vanilla + mods but stat does not.
Every check written as exists()/is_file()/is_dir() therefore answered
"no" about files that were plainly there — and, on the write side,
made mkdir's exist_ok stop working.

usvfs can't run in a test, so these blind stat directly and assert the
helpers still behave. That's the whole contract: enumeration and open
are the sources of truth, stat is not consulted.
"""

import os

import pytest

from esplib.utils import ensure_dir, file_size, is_readable_file


@pytest.fixture
def blind_stat(monkeypatch):
    """Make stat deny paths containing a marker, as usvfs effectively
    does for mod-supplied entries, while open() and mkdir keep working
    against the real filesystem."""
    def apply(marker):
        real_stat = os.stat
        real_isdir = os.path.isdir

        def fake_stat(path, *a, **k):
            if marker.lower() in str(path).lower():
                raise FileNotFoundError(2, "No such file or directory")
            return real_stat(path, *a, **k)

        def fake_isdir(path):
            if marker.lower() in str(path).lower():
                return False
            return real_isdir(path)

        monkeypatch.setattr(os, "stat", fake_stat)
        monkeypatch.setattr(os.path, "isdir", fake_isdir)
    return apply


class TestIsReadableFile:

    def test_finds_a_file_stat_denies(self, tmp_path, blind_stat):
        target = tmp_path / "FurryMod.esp"
        target.write_bytes(b"TES4")
        blind_stat("FurryMod")
        assert not target.exists(), "precondition: stat must be blind"
        assert is_readable_file(target)

    def test_false_for_a_real_absence(self, tmp_path):
        assert not is_readable_file(tmp_path / "Nope.esp")

    def test_false_for_a_directory(self, tmp_path):
        (tmp_path / "Weird.esp").mkdir()
        assert not is_readable_file(tmp_path / "Weird.esp")


class TestEnsureDir:

    def test_creates_nested_directories(self, tmp_path):
        target = tmp_path / "meshes" / "FaceGeom" / "ETaC - Complete.esp"
        ensure_dir(target)
        assert target.is_dir()

    def test_idempotent(self, tmp_path):
        target = tmp_path / "a" / "b"
        ensure_dir(target)
        ensure_dir(target)
        assert target.is_dir()

    def test_existing_dir_that_stat_denies_is_not_an_error(self, tmp_path,
                                                           blind_stat):
        """The MO2 case: mkdir fails with EEXIST because a mod already
        supplies the directory, and stat won't confirm it. mkdir's own
        exist_ok re-raises here; ensure_dir must not."""
        target = tmp_path / "FaceGeom" / "ETaC - Complete.esp"
        target.mkdir(parents=True)
        blind_stat("ETaC")

        with pytest.raises(FileExistsError):
            target.mkdir(parents=True, exist_ok=True)   # the old behaviour

        ensure_dir(target)                              # must not raise

    def test_reports_a_genuine_failure(self, tmp_path):
        """A leaf that can't be created because a *file* sits there is a
        real error and must still surface."""
        blocker = tmp_path / "notadir"
        blocker.write_bytes(b"")
        with pytest.raises(OSError):
            ensure_dir(blocker / "child")


class TestFileSize:
    """Sizing a file is a diagnostic, never a gate.

    `log.debug("... %d bytes", os.path.getsize(p))` evaluates getsize
    whether or not debug is enabled. Under MO2 that stat fails for a
    file just written successfully, so a *disabled* log statement
    raised WinError 3 and discarded 4685 completed facegen bakes.
    """

    def test_returns_a_size(self, tmp_path):
        target = tmp_path / "baked.nif"
        target.write_bytes(b"x" * 1234)
        assert file_size(target) == 1234

    def test_none_when_stat_is_blind(self, tmp_path, blind_stat):
        target = tmp_path / "FaceGeom" / "baked.nif"
        target.parent.mkdir()
        target.write_bytes(b"x" * 10)
        blind_stat("FaceGeom")
        assert file_size(target) is None, "must not raise"

    def test_none_for_a_missing_directory(self, tmp_path):
        """WinError 3 (ERROR_PATH_NOT_FOUND), the shape seen in the
        wild — distinct from a missing file's WinError 2."""
        assert file_size(tmp_path / "no" / "such" / "dir" / "x.nif") is None

from __future__ import annotations

from pathlib import Path

import pytest

import src.io_csv as io_csv


def _configure_tmp_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(io_csv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(io_csv, "BACKUP_DIR", tmp_path / "backup")
    monkeypatch.setattr(io_csv.time, "sleep", lambda _seconds: None)


def test_atomic_write_retries_permission_error(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "sample.csv"
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(self, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("locked")
        return original_replace(self, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    io_csv._atomic_write(target, [{"value": "1"}], ["value"], backup=False)
    assert attempts == 3
    assert target.read_text(encoding="utf-8").splitlines() == ["value", "1"]


def test_atomic_write_reports_file_lock(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "sample.csv"
    monkeypatch.setattr(Path, "replace", lambda *_args: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError, match="Excel"):
        io_csv._atomic_write(target, [{"value": "1"}], ["value"], backup=False)


def test_atomic_write_prunes_old_backups(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "sample.csv"
    target.write_text("value\nold\n", encoding="utf-8")
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    for index in range(io_csv._BACKUP_KEEP_PER_FILE):
        (backup_dir / f"sample_20260530T000000{index:06d}+0900.csv").write_text(
            "old", encoding="utf-8"
        )
    io_csv._atomic_write(target, [{"value": "new"}], ["value"])
    assert len(list(backup_dir.glob("sample_*.csv"))) == io_csv._BACKUP_KEEP_PER_FILE

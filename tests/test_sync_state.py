"""Tests for sync's last_sync.txt state file.

The tray's icon colour reads this file. Until recently, only the tray's
`_action_sync` wrote it — CLI sync left it stale. A failed tray crash
from days ago would leave the icon stuck on red forever even after
successful CLI syncs. The fix moved the write into sync.run() so every
invocation keeps the file honest. These tests pin that contract.
"""
from __future__ import annotations


from brewbridge.core import sync as bb_sync


def test_record_sync_state_writes_ok(monkeypatch, tmp_path):
    """`_record_sync_state('ok')` produces the format the tray reader
    expects: <unix_ts>\\n<status>\\n."""
    monkeypatch.setattr(bb_sync, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bb_sync, "LAST_SYNC_FILE", tmp_path / "last_sync.txt")

    bb_sync._record_sync_state("ok")

    content = (tmp_path / "last_sync.txt").read_text(encoding="utf-8")
    lines = content.strip().split("\n")
    assert len(lines) == 2
    # First line is a unix-timestamp float
    float(lines[0])
    assert lines[1] == "ok"


def test_record_sync_state_writes_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(bb_sync, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bb_sync, "LAST_SYNC_FILE", tmp_path / "last_sync.txt")

    bb_sync._record_sync_state("failed")

    lines = (tmp_path / "last_sync.txt").read_text(encoding="utf-8").strip().split("\n")
    assert lines[1] == "failed"


def test_record_sync_state_overwrites_previous(monkeypatch, tmp_path):
    """A failed sync must be able to overwrite an earlier 'ok' file
    (and vice versa). This is the bug we hit: tray crash wrote 'failed',
    no subsequent CLI sync ever updated it, icon stayed red."""
    monkeypatch.setattr(bb_sync, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bb_sync, "LAST_SYNC_FILE", tmp_path / "last_sync.txt")

    bb_sync._record_sync_state("failed")
    bb_sync._record_sync_state("ok")

    lines = (tmp_path / "last_sync.txt").read_text(encoding="utf-8").strip().split("\n")
    assert lines[1] == "ok"


def test_record_sync_state_creates_data_dir_if_missing(monkeypatch, tmp_path):
    """Fresh install: ~/.brewbridge/ doesn't exist yet. First sync
    has to create it. (mkdir(parents=True, exist_ok=True) on the
    DATA_DIR write side.)"""
    missing_dir = tmp_path / "deep" / "not-yet-created"
    monkeypatch.setattr(bb_sync, "DATA_DIR", missing_dir)
    monkeypatch.setattr(bb_sync, "LAST_SYNC_FILE", missing_dir / "last_sync.txt")

    bb_sync._record_sync_state("ok")

    assert missing_dir.exists()
    assert (missing_dir / "last_sync.txt").exists()


def test_run_purge_builtins_defaults_to_false():
    """Pin the v0.1.3 default change. The previous default deleted every
    non-(brew.is) library row on first sync — an experienced BeerSmith
    user installing brewbridge would silently lose their custom hops,
    yeasts, and grains. Non-destructive is now the default; the
    destructive mode is gated behind --brew-is-only.
    """
    import inspect
    sig = inspect.signature(bb_sync.run)
    assert sig.parameters["purge_builtins"].default is False, (
        "sync.run() must default to non-destructive (purge_builtins=False). "
        "Destructive mode should require explicit opt-in via the "
        "--brew-is-only CLI flag.")


def test_run_records_failed_on_exception(monkeypatch, tmp_path):
    """sync.run() wraps _run_inner; any exception triggers a 'failed'
    write before re-raising. Without this, CLI sync that fails leaves
    last_sync.txt stale (potentially still showing the previous 'ok')."""
    monkeypatch.setattr(bb_sync, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bb_sync, "LAST_SYNC_FILE", tmp_path / "last_sync.txt")

    # Pre-seed with 'ok' so we can detect the overwrite
    bb_sync._record_sync_state("ok")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated sync failure")

    monkeypatch.setattr(bb_sync, "_run_inner", boom)

    try:
        bb_sync.run()
        assert False, "should have re-raised"
    except RuntimeError as e:
        assert "simulated sync failure" in str(e)

    lines = (tmp_path / "last_sync.txt").read_text(encoding="utf-8").strip().split("\n")
    assert lines[1] == "failed", \
        f"expected last_sync.txt to be overwritten to 'failed', got {lines[1]!r}"

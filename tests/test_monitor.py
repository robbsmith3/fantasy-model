import base64
from datetime import UTC, datetime, timedelta

from fantasy_model import monitor

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _run(con, job, ok, ago):
    t = NOW - ago
    con.execute(
        "INSERT INTO snapshot_run VALUES (?,?,2026,10,?,?,?,NULL)",
        [f"{t:%Y-%m-%dT%H:%M}_{job}", job, t, t, ok],
    )


def test_alerts_flag_a_failed_last_run_and_overdue_jobs(con):
    _run(con, "daily", True, timedelta(hours=20))
    _run(con, "pre_waiver", True, timedelta(days=6))
    _run(con, "post_waiver", True, timedelta(days=12))  # overdue
    _run(con, "post_waiver", False, timedelta(days=5))  # and the latest attempt failed
    a = monitor.alerts(con, NOW)
    jobs = {(x["level"], x["job"]) for x in a}
    assert ("error", "post_waiver") in jobs and ("warning", "post_waiver") in jobs
    assert not any(x["job"] in ("daily", "pre_waiver") for x in a)
    assert not any(x["job"] == "backup" for x in a)  # never ran yet: not an alert


def test_a_backup_that_only_ever_failed_is_flagged(con):
    for job in ("daily", "pre_waiver", "post_waiver"):
        _run(con, job, True, timedelta(hours=1))
    _run(con, "backup", False, timedelta(hours=1))
    a = {(x["level"], x["job"]) for x in monitor.alerts(con, NOW)}
    assert a == {("error", "backup"), ("warning", "backup")}


def test_notify_quotes_text_safely_and_never_raises(monkeypatch, tmp_path):
    exe = tmp_path / "powershell.exe"
    exe.write_text("")
    seen = {}

    def fake_run(cmd, **kw):
        seen["script"] = base64.b64decode(cmd[-1]).decode("utf-16-le")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(monitor.shutil, "which", lambda name: str(exe))
    monkeypatch.setattr(monitor.subprocess, "run", fake_run)
    assert monitor.notify("Job's down", "it's 'broken'")
    assert "Job''s down" in seen["script"] and "it''s ''broken''" in seen["script"]

    def boom(*a, **k):
        raise OSError("no powershell")

    monkeypatch.setattr(monitor.subprocess, "run", boom)
    assert monitor.notify("x", "y") is False


def test_a_crash_with_the_database_locked_is_saved_then_alerted_then_recorded(
    con, tmp_path, monkeypatch
):
    path = tmp_path / "failed_runs.jsonl"
    monkeypatch.setattr(monitor, "_failed_runs_path", lambda: path)
    monkeypatch.setattr(monitor, "notify", lambda *a: True)

    def locked(*a, **k):
        raise OSError("Conflicting lock")

    monkeypatch.setattr(monitor, "record", locked)
    monitor.report_crash("pre_waiver", "Traceback\nIOException: Conflicting lock")
    assert path.exists()

    _run(con, "pre_waiver", True, timedelta(days=1))  # the earlier success doesn't hide it
    assert any(x["level"] == "error" and x["job"] == "pre_waiver" for x in monitor.alerts(con, NOW))

    monitor.flush_failures(con)
    assert not path.exists()
    row = con.execute("SELECT ok, error FROM snapshot_run WHERE ok = FALSE").fetchall()
    assert row[0][1].endswith("Conflicting lock")


def test_a_crash_is_recorded_directly_when_the_database_is_free(monkeypatch, tmp_path):
    calls = []
    path = tmp_path / "failed_runs.jsonl"
    monkeypatch.setattr(monitor, "_failed_runs_path", lambda: path)
    monkeypatch.setattr(monitor, "notify", lambda *a: calls.append(a) or True)
    monkeypatch.setattr(monitor, "record", lambda *a, **k: calls.append(a))
    monitor.report_crash("daily", "boom")
    assert len(calls) == 2 and calls[1][:2] == ("daily", False) and not path.exists()

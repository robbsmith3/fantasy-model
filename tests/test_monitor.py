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

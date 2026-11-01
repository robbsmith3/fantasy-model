"""Job monitoring: a desktop notification the moment a job fails, and alerts for the web app when
a job failed or hasn't run for longer than its schedule allows (PC off, task missing, WSL down).
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

from fantasy_model import db

POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

# Longest healthy gap between successful runs. `daily` skips Thu/Sun, so 3 days covers it;
# weekly jobs get a day of slack.
MAX_GAP = {
    "daily": timedelta(days=3),
    "pre_waiver": timedelta(days=8),
    "post_waiver": timedelta(days=8),
    "backup": timedelta(days=8),
    "update": timedelta(days=15),  # weekly; a missed week is fine
}
OPTIONAL = {"backup", "update"}  # not flagged until they've run once (setup may not include them)

_TOAST = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode('{title}')) > $null
$x.Item(1).AppendChild($t.CreateTextNode('{body}')) > $null
$app = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""  # noqa: E501


def notify(title: str, body: str) -> bool:
    """Windows toast from WSL via PowerShell; False (silently) when that isn't possible."""
    exe = shutil.which("powershell.exe") or POWERSHELL
    if not os.path.exists(exe) and not shutil.which(exe):
        return False

    def q(s: str) -> str:  # PowerShell single-quoted literal
        return s.replace("'", "''")[:240]

    script = _TOAST.format(title=q(title), body=q(body))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        r = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=30,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def record(job: str, ok: bool, error: str | None = None) -> None:
    """Log a non-snapshot job (e.g. backup) in snapshot_run so health and alerts see it."""
    con = db.connect()
    try:
        last = con.execute(
            "SELECT season, nfl_week FROM snapshot_run ORDER BY started_at DESC LIMIT 1"
        ).fetchall()
        season, week = last[0] if last else (datetime.now().year, 0)
        now = db.now_utc()
        con.execute(
            "INSERT OR REPLACE INTO snapshot_run VALUES (?,?,?,?,?,?,?,?)",
            [f"{now:%Y-%m-%dT%H:%MZ}_{job}", job, season, week, now, now, ok, error],
        )
    finally:
        con.close()


def _label(job: str) -> str:
    return job.replace("_", " ")


def alerts(con, now: datetime | None = None) -> list[dict]:
    """[{level, job, message}] for the web app: last run failed, or overdue for its schedule."""
    now = now or datetime.now(UTC)
    rows = con.execute(
        """SELECT job, arg_max(ok, started_at), max(started_at) FILTER (WHERE ok)
           FROM snapshot_run GROUP BY job"""
    ).fetchall()
    last_ok = {job: ok for job, ok, _ in rows}
    last_success = {job: t for job, _, t in rows}
    out = []
    for job, ok in last_ok.items():
        if ok is False:
            out.append(
                {"level": "error", "job": job, "message": f"The last {_label(job)} run failed."}
            )
    for job, gap in MAX_GAP.items():
        success = last_success.get(job)
        if success is None:
            if job in last_ok or job not in OPTIONAL:
                msg = f"No successful {_label(job)} run yet."
                out.append({"level": "warning", "job": job, "message": msg})
            continue
        if success.tzinfo is None:
            success = success.replace(tzinfo=UTC)
        if now - success > gap:
            msg = (
                f"{_label(job).capitalize()} hasn't succeeded in {(now - success).days} days. "
                "Is the PC on and the task scheduled?"
            )
            out.append({"level": "warning", "job": job, "message": msg})
    return out

#!/usr/bin/env bash
# Pull changes merged on GitHub (incl. Dependabot updates), reinstall, rebuild the web app, run the
# tests, and restart the site task. Any failure rolls back to the previous commit and raises an
# alert. Run weekly by the "update" task (scripts/register_tasks.ps1), or by hand.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
# Node from nvm (the apt Node is too old for the web build); login shells skip ~/.bashrc.
# shellcheck disable=SC1091
source "$HOME/.nvm/nvm.sh" && nvm use -s default >/dev/null
REPO="https://github.com/robbsmith3/fantasy-model.git"  # public: no SSH key needed unattended
PS="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

report() {  # report OK|FAIL message
    uv run python -c "import sys; from fantasy_model import monitor
ok = sys.argv[1] == 'OK'; monitor.record('update', ok, None if ok else sys.argv[2])
ok or monitor.notify('Chop Desk: update failed', sys.argv[2])" "$1" "$2"
    echo "$(date '+%F %T') update $1: $2"
}

build() { uv sync --locked -q && (cd web && npm ci --silent && npm run build --silent) >/dev/null; }

restart_site() {  # only if the logon task exists (scripts/register_tasks.ps1)
    # Stopping the task doesn't reach the server inside WSL, so end that here or the port stays taken.
    pkill -f "ffm serve" || true
    "$PS" -NoProfile -Command "\$t = Get-ScheduledTask -TaskPath '\FantasyModel\' -TaskName site \
        -ErrorAction SilentlyContinue; if (\$t) { Stop-ScheduledTask -InputObject \$t; \
        Start-Sleep 2; Start-ScheduledTask -InputObject \$t }" >/dev/null 2>&1 || true
}

old=$(git rev-parse HEAD)
git fetch -q "$REPO" main || { report FAIL "could not reach GitHub"; exit 1; }
new=$(git rev-parse FETCH_HEAD)
[ "$old" = "$new" ] && { report OK "already up to date"; exit 0; }
if ! git diff --quiet HEAD || ! git merge-base --is-ancestor HEAD FETCH_HEAD; then
    report FAIL "local changes or commits not on GitHub; push or resolve, then rerun"; exit 1
fi
git merge -q --ff-only FETCH_HEAD
if build && uv run pytest -q -p no:cacheprovider >/dev/null; then
    restart_site
    report OK "updated ${old:0:7} -> ${new:0:7}"
else
    git reset -q --hard "$old" && build
    report FAIL "update to ${new:0:7} failed its checks; rolled back to ${old:0:7}"
    exit 1
fi

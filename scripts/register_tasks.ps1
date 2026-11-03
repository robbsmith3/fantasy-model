# Registers the snapshot schedule in Windows Task Scheduler (run in PowerShell, NOT admin needed).
# Why not cron inside WSL: the WSL VM shuts down when idle, so cron silently misses runs.
# Task Scheduler launches WSL on demand, can wake the PC, and catches up missed runs on boot.
#
# Times are Windows LOCAL time; check each league's waiver run time.
# Tasks start through `conhost --headless` so no console window opens at logon or on catch-up runs.
# If a task already exists and was registered from an elevated (admin) shell, re-run this elevated.

$Distro  = "Ubuntu-24.04"
$Project = "~/code/fantasy-model"
$Folder  = "\FantasyModel\"

# $Job is the ffm subcommand line, e.g. "run daily" or "history"; $Override replaces the whole command.
function Register-Snap($Name, $Job, $Trigger, $Override = $null) {
    $cmd = "cd $Project && mkdir -p logs && ~/.local/bin/uv run ffm $Job >> logs/snapshots.log 2>&1"
    if ($Override) { $cmd = "cd $Project && mkdir -p logs && $Override" }
    $action = New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless wsl.exe -d $Distro -e bash -lc `"$cmd`""
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
    # New-ScheduledTaskTrigger pins today's UTC offset (e.g. -05:00), so after a DST change every
    # task fires an hour off. Drop the offset so the trigger follows local wall-clock time.
    $Trigger.StartBoundary = ([datetime]$Trigger.StartBoundary).ToString("yyyy-MM-dd'T'HH:mm:ss")
    try {
        Register-ScheduledTask -TaskPath $Folder -TaskName $Name -Action $action `
            -Trigger $Trigger -Settings $settings -Force -ErrorAction Stop | Out-Null
        Write-Host "registered $Folder$Name"
    } catch {
        Write-Host "FAILED     $Folder$Name : $($_.Exception.Message)"
    }
}

# daily: not Thu/Sun (pre_kickoff pulls players those days -> stays within Sleeper's 1 call/day)
Register-Snap "daily"       "run daily"       (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Friday,Saturday -At 9am)
Register-Snap "pre_waiver"  "run pre_waiver"  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday  -At 8pm)
Register-Snap "post_waiver" "run post_waiver" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Wednesday -At 9am)
Register-Snap "pre_kickoff_thu" "run pre_kickoff" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Thursday -At 6:30pm)
Register-Snap "pre_kickoff_sun" "run pre_kickoff" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday   -At 11:30am)
# history: refresh current season nflverse stats/snaps/schedules after MNF data lands
Register-Snap "history"     "history"         (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday  -At 10am)

# update: pull changes merged on GitHub (incl. Dependabot), rebuild, test; rolls back on failure
Register-Snap "update" "" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Wednesday -At 11am) `
    "bash scripts/update.sh >> logs/update.log 2>&1"

# site: `ffm serve` at logon, hidden, restarted if it crashes. Stopping the task leaves the server
# running inside WSL, so stop it with
#   wsl.exe -d Ubuntu-24.04 -e pkill -f "ffm serve"
$serve = "cd $Project && mkdir -p logs && ~/.local/bin/uv run ffm serve >> logs/serve.log 2>&1"
$siteAction = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless wsl.exe -d $Distro -e bash -lc `"$serve`""
$siteSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
try {
    Register-ScheduledTask -TaskPath $Folder -TaskName "site" -Action $siteAction `
        -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
        -Settings $siteSettings -Force -ErrorAction Stop | Out-Null
    wsl.exe -d $Distro -e pkill -f "ffm serve"  # free the port from any earlier run
    Start-ScheduledTask -TaskPath $Folder -TaskName "site"
    Write-Host "registered $($Folder)site (running now: http://127.0.0.1:8000)"
} catch {
    Write-Host "FAILED     $($Folder)site : $($_.Exception.Message)"
}

Write-Host "`nTest one now:  Start-ScheduledTask -TaskPath '$Folder' -TaskName daily"
Write-Host "Remove all:    Get-ScheduledTask -TaskPath '$Folder' | Unregister-ScheduledTask -Confirm:`$false"

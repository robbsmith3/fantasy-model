# Registers the snapshot schedule in Windows Task Scheduler (run in PowerShell, NOT admin needed).
# Why not cron inside WSL: the WSL VM shuts down when idle, so cron silently misses runs.
# Task Scheduler launches WSL on demand, can wake the PC, and catches up missed runs on boot.
#
# Times are Windows LOCAL time. Defaults assume Eastern; check each league's waiver run time.

$Distro  = "Ubuntu-24.04"
$Project = "~/code/fantasy-model"
$Folder  = "\FantasyModel\"

function Register-Snap($Name, $Job, $Trigger) {
    $cmd = "cd $Project && mkdir -p logs && ~/.local/bin/uv run ffm run $Job >> logs/snapshots.log 2>&1"
    $action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro -- bash -lc `"$cmd`""
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskPath $Folder -TaskName $Name -Action $action `
        -Trigger $Trigger -Settings $settings -Force | Out-Null
    Write-Host "registered $Folder$Name"
}

# daily: not Thu/Sun (pre_kickoff pulls players those days -> stays within Sleeper's 1 call/day)
Register-Snap "daily"       "daily"       (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Friday,Saturday -At 9am)
Register-Snap "pre_waiver"  "pre_waiver"  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday  -At 8pm)
Register-Snap "post_waiver" "post_waiver" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Wednesday -At 9am)
Register-Snap "pre_kickoff_thu" "pre_kickoff" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Thursday -At 6:30pm)
Register-Snap "pre_kickoff_sun" "pre_kickoff" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday   -At 11:30am)

Write-Host "`nTest one now:  Start-ScheduledTask -TaskPath '$Folder' -TaskName daily"
Write-Host "Remove all:    Get-ScheduledTask -TaskPath '$Folder' | Unregister-ScheduledTask -Confirm:`$false"

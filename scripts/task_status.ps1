# Read-only: show last/next run and result code for every FantasyModel task.
Get-ScheduledTask -TaskPath '\FantasyModel\' | Get-ScheduledTaskInfo |
    Format-Table TaskName, LastRunTime, LastTaskResult, NextRunTime -AutoSize

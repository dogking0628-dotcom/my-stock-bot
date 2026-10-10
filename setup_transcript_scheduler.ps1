# 建立每日排程：23:00 跑 run_transcripts.bat（抓頻道逐字稿 → 分析 → 對帳 → push）
# 特點：StartWhenAvailable = 錯過 23:00（電腦沒開）就在下次開機後自動補跑；
#       輸出寫到 run_transcripts.log 方便查問題；同時間不重複啟動；最長跑 3 小時。
# 由 setup_transcript_scheduler.bat 呼叫（右鍵「以系統管理員身分執行」）。

$ErrorActionPreference = 'Stop'
$dir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat  = Join-Path $dir 'run_transcripts.bat'
$log  = Join-Path $dir 'run_transcripts.log'
$name = 'InvestBot_Transcripts'
$time = '23:00'

if (-not (Test-Path $bat)) { throw "找不到 $bat" }

$action   = New-ScheduledTaskAction -Execute 'cmd.exe' `
              -Argument "/c `"`"$bat`" > `"$log`" 2>&1`"" -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
              -ExecutionTimeLimit (New-TimeSpan -Hours 3) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description '每日抓 YouTube 財經頻道逐字稿 → Claude 論點入庫 → 到期對帳 → push' -Force | Out-Null

$task = Get-ScheduledTask -TaskName $name
$info = $task | Get-ScheduledTaskInfo
Write-Host ""
Write-Host "[OK] 排程「$name」已建立" -ForegroundColor Green
Write-Host "     每天 $time 執行；電腦沒開會在下次開機後補跑"
Write-Host "     狀態：$($task.State)　下次執行：$($info.NextRunTime)"
Write-Host "     執行紀錄：$log"
Write-Host ""
Write-Host "想現在立刻試跑一次：schtasks /run /tn $name"

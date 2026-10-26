# gpu-sentiment.ps1 을 Windows 작업 스케줄러에 30분마다 등록한다 (현재 사용자, 로그인해 있을 때만). 지우기:
#   Unregister-ScheduledTask -TaskName "NEXUS GPU sentiment" -Confirm:$false
$script = Join-Path $PSScriptRoot "gpu-sentiment.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 30)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "NEXUS GPU sentiment" -Action $action -Trigger $trigger -Settings $settings `
    -Description "KR-FinBERT 기사 감성을 이 PC GPU 로 매겨 NEXUS GCP 서버에 올린다 (deploy/nexus-realtime/gpu-sentiment.ps1)" -Force

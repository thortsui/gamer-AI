Add-Type -Name P -Namespace W -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
$end = Get-Date -Hour 23 -Minute 10 -Second 0
while ((Get-Date) -lt $end) { [W.P]::SetThreadExecutionState(0x80000003) | Out-Null; Start-Sleep 50 }
[W.P]::SetThreadExecutionState(0x80000000) | Out-Null

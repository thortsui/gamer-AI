Add-Type -Name P -Namespace W -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
$end = (Get-Date).Date.AddDays(1).AddHours(23).AddMinutes(10)
while ((Get-Date) -lt $end) { [W.P]::SetThreadExecutionState(0x80000003) | Out-Null; Start-Sleep 50 }
[W.P]::SetThreadExecutionState(0x80000000) | Out-Null

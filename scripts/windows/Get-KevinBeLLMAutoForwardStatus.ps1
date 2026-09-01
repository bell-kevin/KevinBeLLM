# SPDX-License-Identifier: AGPL-3.0-or-later
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$WebLocalPort = 3000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$taskName = "KevinBeLLM Auto Forward"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$taskInfo = if ($null -ne $task) {
    Get-ScheduledTaskInfo -TaskName $taskName
} else {
    $null
}
$listener = Get-NetTCPConnection `
    -LocalAddress "127.0.0.1" `
    -LocalPort $WebLocalPort `
    -State Listen `
    -ErrorAction SilentlyContinue

$applicationHealthy = $false
if ($null -ne $listener) {
    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$WebLocalPort/health" `
            -TimeoutSec 5
        $applicationHealthy = $response.StatusCode -eq 200
    } catch {
        $applicationHealthy = $false
    }
}

$summary = [pscustomobject]@{
    ScheduledTask = if ($null -eq $task) { "Not installed" } else { [string]$task.State }
    Tunnel = if ($null -eq $listener) { "Not listening" } else { "Listening on 127.0.0.1:$WebLocalPort" }
    KevinBeLLM = if ($applicationHealthy) { "Healthy" } else { "Unavailable" }
    LastTaskRun = if ($null -eq $taskInfo) { $null } else { $taskInfo.LastRunTime }
    LastTaskResult = if ($null -eq $taskInfo) { $null } else { $taskInfo.LastTaskResult }
}
$summary | Format-List

if ($null -eq $task -or $task.State -ne "Running" -or -not $applicationHealthy) {
    exit 1
}

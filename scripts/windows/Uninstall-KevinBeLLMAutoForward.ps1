# SPDX-License-Identifier: AGPL-3.0-or-later
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$taskName = "KevinBeLLM Auto Forward"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "'$taskName' is not installed."
    return
}

$removed = $false
if ($PSCmdlet.ShouldProcess($taskName, "stop and unregister the scheduled task")) {
    if ($task.State -ne "Ready") {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    $removed = $true
}

if ($removed) {
    Write-Host "Removed '$taskName'."
    Write-Host "The Zoo Code API token and SSH configuration were not changed."
}

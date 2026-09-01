# SPDX-License-Identifier: AGPL-3.0-or-later
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidatePattern("^[A-Za-z0-9._-]+$")][string]$SshAlias = "kevinbellm-a",
    [ValidateRange(1, 65535)][int]$WebLocalPort = 3000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$taskName = "KevinBeLLM Auto Forward"
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$forwardScript = Join-Path $PSScriptRoot "Open-KevinBeLLMForward.ps1"
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

foreach ($requiredPath in @($forwardScript, $powerShellExe)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required file not found: $requiredPath"
    }
    if ($requiredPath.Contains('"')) {
        throw "Task paths cannot contain a double quote: $requiredPath"
    }
}

$null = Get-Command ssh.exe -ErrorAction Stop
$null = Get-Command Register-ScheduledTask -ErrorAction Stop

Write-Host "Checking non-interactive SSH access to $SshAlias..."
& ssh.exe `
    -T `
    -o BatchMode=yes `
    -o ConnectionAttempts=1 `
    -o ConnectTimeout=10 `
    -o StrictHostKeyChecking=yes `
    $SshAlias "exit 0"
if ($LASTEXITCODE -ne 0) {
    throw @"
Non-interactive SSH access failed. Run 'ssh $SshAlias' once, verify the host
fingerprint, and make sure the configured key does not require an unavailable
interactive password prompt. Then rerun this installer.
"@
}

$actionArguments = @(
    "-NoLogo"
    "-NoProfile"
    "-NonInteractive"
    "-WindowStyle Hidden"
    "-ExecutionPolicy Bypass"
    "-File `"$forwardScript`""
    "-SshAlias $SshAlias"
    "-WebLocalPort $WebLocalPort"
    "-AutoReconnect"
) -join " "

$action = New-ScheduledTaskAction `
    -Execute $powerShellExe `
    -Argument $actionArguments `
    -WorkingDirectory (Split-Path -Parent $forwardScript)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$installed = $false
if ($PSCmdlet.ShouldProcess($taskName, "register and start the per-user logon task")) {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Maintains the loopback-only KevinBeLLM SSH tunnel for Zoo Code." `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    $installed = $true
}

if ($installed) {
    Write-Host "Installed '$taskName' for $currentUser."
    Write-Host "It starts hidden at logon and reconnects automatically."
    Write-Host "The task stores no SSH password or KevinBeLLM API token."
    Write-Host "If this repository moves, rerun this installer from its new location."
}

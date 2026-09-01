# SPDX-License-Identifier: AGPL-3.0-or-later
[CmdletBinding()]
param(
    [string]$SshAlias = "kevinbellm-a",
    [ValidateRange(1, 65535)][int]$WebLocalPort = 3000,
    [switch]$ForwardLlamaApi,
    [ValidateRange(1, 65535)][int]$LlamaLocalPort = 18080,
    [switch]$AutoReconnect,
    [ValidateRange(1, 300)][int]$InitialRetrySeconds = 5,
    [ValidateRange(1, 3600)][int]$MaximumRetrySeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$null = Get-Command ssh.exe -ErrorAction Stop

if ($AutoReconnect -and $ForwardLlamaApi) {
    throw "Automatic mode never exposes the unauthenticated llama.cpp diagnostic API."
}

if (-not $AutoReconnect) {
    Write-Host "Opening loopback-only forwards through Machine A. Keep this window open."
    Write-Host "KevinBeLLM UI: http://127.0.0.1:$WebLocalPort"
    Write-Host "Zoo Code API (login-issued token required): http://127.0.0.1:$WebLocalPort/v1"
}

$sshArgs = @(
    "-N",
    "-T",
    "-o", "ExitOnForwardFailure=yes",
    "-o", $(if ($AutoReconnect) { "ServerAliveInterval=15" } else { "ServerAliveInterval=30" }),
    "-o", $(if ($AutoReconnect) { "ServerAliveCountMax=2" } else { "ServerAliveCountMax=3" }),
    "-L", "127.0.0.1:${WebLocalPort}:127.0.0.1:3000"
)
if ($AutoReconnect) {
    # A hidden scheduled task cannot answer password or host-key prompts. The
    # installer verifies this exact non-interactive connection before it
    # registers the task.
    $sshArgs = @(
        "-n",
        "-o", "BatchMode=yes",
        "-o", "ConnectionAttempts=1",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=yes"
    ) + $sshArgs
}
if ($ForwardLlamaApi) {
    Write-Warning "The raw llama.cpp diagnostic API has no KevinBeLLM login. Never use it as Zoo Code's Base URL."
    Write-Host "llama.cpp API: http://127.0.0.1:$LlamaLocalPort"
    $sshArgs += @("-L", "127.0.0.1:${LlamaLocalPort}:127.0.0.1:8080")
}
$sshArgs += $SshAlias

$autoMutex = $null
$ownsAutoMutex = $false
if ($AutoReconnect) {
    $autoMutex = [Threading.Mutex]::new($false, "Local\KevinBeLLMForward-$WebLocalPort")
    try {
        $ownsAutoMutex = $autoMutex.WaitOne(0)
    } catch [Threading.AbandonedMutexException] {
        $ownsAutoMutex = $true
    }
    if (-not $ownsAutoMutex) {
        $autoMutex.Dispose()
        return
    }
}

try {
    $retrySeconds = $InitialRetrySeconds
    while ($true) {
        $startedAt = [DateTime]::UtcNow
        & ssh.exe @sshArgs
        $exitCode = $LASTEXITCODE

        if (-not $AutoReconnect) {
            if ($exitCode -ne 0) {
                throw "SSH forwarding stopped with exit code $exitCode."
            }
            break
        }

        $connectedSeconds = ([DateTime]::UtcNow - $startedAt).TotalSeconds
        if ($connectedSeconds -ge 60) {
            $retrySeconds = $InitialRetrySeconds
        }
        $delaySeconds = $retrySeconds
        $retrySeconds = [Math]::Min($MaximumRetrySeconds, $retrySeconds * 2)
        Start-Sleep -Seconds $delaySeconds
    }
} finally {
    if ($ownsAutoMutex) {
        $autoMutex.ReleaseMutex()
    }
    if ($null -ne $autoMutex) {
        $autoMutex.Dispose()
    }
}

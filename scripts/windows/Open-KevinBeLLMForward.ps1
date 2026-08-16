# SPDX-License-Identifier: AGPL-3.0-or-later
[CmdletBinding()]
param(
    [string]$SshAlias = "kevinbellm-a",
    [ValidateRange(1, 65535)][int]$WebLocalPort = 3000,
    [switch]$ForwardLlamaApi,
    [ValidateRange(1, 65535)][int]$LlamaLocalPort = 18080
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$null = Get-Command ssh.exe -ErrorAction Stop

Write-Host "Opening loopback-only forwards through Machine A. Keep this window open."
Write-Host "KevinBeLLM UI: http://127.0.0.1:$WebLocalPort"

$sshArgs = @(
    "-NT",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-L", "127.0.0.1:${WebLocalPort}:127.0.0.1:3000"
)
if ($ForwardLlamaApi) {
    Write-Host "llama.cpp API: http://127.0.0.1:$LlamaLocalPort"
    $sshArgs += @("-L", "127.0.0.1:${LlamaLocalPort}:127.0.0.1:8080")
}
$sshArgs += $SshAlias
& ssh.exe @sshArgs
if ($LASTEXITCODE -ne 0) {
    throw "SSH forwarding stopped with exit code $LASTEXITCODE."
}

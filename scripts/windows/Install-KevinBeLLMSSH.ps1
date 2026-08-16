# SPDX-License-Identifier: AGPL-3.0-or-later
[CmdletBinding()]
param(
    [string]$InventoryFile = "",
    [string]$MachineAHost = "",
    [string]$MachineBHost = "",
    [string]$MachineAUser = "",
    [string]$MachineBUser = "",
    [int]$MachineAPort = 0,
    [int]$MachineBPort = 0,
    [string]$IdentityFile = "$HOME\.ssh\id_ed25519_kevinbellm_admin",
    [switch]$GenerateKey,
    [switch]$InstallPublicKey
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Import-SimpleEnv {
    param([Parameter(Mandatory = $true)][string]$Path)
    $values = @{}
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith("#")) { continue }
        $parts = $line.Split("=", 2)
        if ($parts.Count -ne 2) { throw "Invalid inventory line: $rawLine" }
        $value = $parts[1].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$parts[0].Trim()] = $value
    }
    return $values
}

if ([string]::IsNullOrWhiteSpace($InventoryFile)) {
    $InventoryFile = Join-Path $PSScriptRoot "..\..\infra\cluster\inventory.env"
}
if (Test-Path -LiteralPath $InventoryFile) {
    $inventory = Import-SimpleEnv -Path $InventoryFile
    if ([string]::IsNullOrWhiteSpace($MachineAHost)) { $MachineAHost = $inventory["MACHINE_A_HOST"] }
    if ([string]::IsNullOrWhiteSpace($MachineBHost)) { $MachineBHost = $inventory["MACHINE_B_HOST"] }
    if ([string]::IsNullOrWhiteSpace($MachineAUser)) { $MachineAUser = $inventory["MACHINE_A_SSH_USER"] }
    if ([string]::IsNullOrWhiteSpace($MachineBUser)) { $MachineBUser = $inventory["MACHINE_B_SSH_USER"] }
    if ($MachineAPort -eq 0) { $MachineAPort = [int]$inventory["MACHINE_A_SSH_PORT"] }
    if ($MachineBPort -eq 0) { $MachineBPort = [int]$inventory["MACHINE_B_SSH_PORT"] }
}

foreach ($required in @{
    MachineAHost = $MachineAHost
    MachineBHost = $MachineBHost
    MachineAUser = $MachineAUser
    MachineBUser = $MachineBUser
}.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace([string]$required.Value) -or [string]$required.Value -like "*CHANGE_ME*") {
        throw "Set $($required.Key) directly or in infra\cluster\inventory.env."
    }
}
if ($MachineAPort -lt 1 -or $MachineAPort -gt 65535 -or
    $MachineBPort -lt 1 -or $MachineBPort -gt 65535) {
    throw "SSH ports must be between 1 and 65535."
}

$null = Get-Command ssh.exe -ErrorAction Stop
$null = Get-Command ssh-keygen.exe -ErrorAction Stop
$sshDirectory = Split-Path -Parent $IdentityFile
New-Item -ItemType Directory -Force -Path $sshDirectory | Out-Null

if (-not (Test-Path -LiteralPath $IdentityFile)) {
    if (-not $GenerateKey) {
        throw "Admin key not found at $IdentityFile. Rerun with -GenerateKey; ssh-keygen will ask for a passphrase."
    }
    Write-Host "Generating the laptop admin key. Choose a strong passphrase when prompted."
    & ssh-keygen.exe -t ed25519 -a 100 -f $IdentityFile -C "kevinbellm-laptop-admin"
    if ($LASTEXITCODE -ne 0) { throw "ssh-keygen failed with exit code $LASTEXITCODE" }
}
if (-not (Test-Path -LiteralPath "$IdentityFile.pub")) {
    throw "Public key is missing: $IdentityFile.pub"
}
$publicKeyRaw = (Get-Content -Raw -LiteralPath "$IdentityFile.pub").Trim()
if ($publicKeyRaw -match "[\r\n]") {
    throw "Public key file must contain exactly one key: $IdentityFile.pub"
}
$publicKeyFields = $publicKeyRaw -split "\s+"
if ($publicKeyFields.Count -lt 2 -or
    $publicKeyFields[0] -ne "ssh-ed25519" -or
    $publicKeyFields[1] -notmatch "^[A-Za-z0-9+/]+={0,3}$") {
    throw "Expected one Ed25519 public key in $IdentityFile.pub"
}
& ssh-keygen.exe -lf "$IdentityFile.pub" -E sha256 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Invalid public key: $IdentityFile.pub" }
$publicKey = "$($publicKeyFields[0]) $($publicKeyFields[1]) kevinbellm-laptop-admin"

$configPath = Join-Path $HOME ".ssh\config"
$beginMarker = "# BEGIN KEVINBELLM CLUSTER"
$endMarker = "# END KEVINBELLM CLUSTER"
$managedBlock = @"
$beginMarker
Host kevinbellm-a
    HostName $MachineAHost
    User $MachineAUser
    Port $MachineAPort
    IdentityFile "$IdentityFile"
    IdentitiesOnly yes
    UpdateHostKeys no
    ServerAliveInterval 30
    ServerAliveCountMax 3

Host kevinbellm-b
    HostName $MachineBHost
    User $MachineBUser
    Port $MachineBPort
    IdentityFile "$IdentityFile"
    IdentitiesOnly yes
    UpdateHostKeys no
    ServerAliveInterval 30
    ServerAliveCountMax 3
$endMarker
"@

$existing = if (Test-Path -LiteralPath $configPath) { Get-Content -Raw -LiteralPath $configPath } else { "" }
$pattern = "(?ms)^" + [regex]::Escape($beginMarker) + ".*?^" + [regex]::Escape($endMarker) + "\r?\n?"
$withoutManagedBlock = [regex]::Replace($existing, $pattern, "").TrimEnd()
$newContent = if ($withoutManagedBlock.Length -gt 0) {
    $withoutManagedBlock + [Environment]::NewLine + [Environment]::NewLine + $managedBlock
} else {
    $managedBlock
}
[IO.File]::WriteAllText($configPath, $newContent, [Text.UTF8Encoding]::new($false))
Write-Host "Installed idempotent SSH aliases kevinbellm-a and kevinbellm-b in $configPath"

if ($InstallPublicKey) {
    # `tr` removes PowerShell's native-pipeline CRLF so authorized_keys receives
    # one canonical line regardless of Windows PowerShell version.
    $remoteInstall = 'umask 077; mkdir -p "$HOME/.ssh"; touch "$HOME/.ssh/authorized_keys"; key=$(tr -d "\r\n"); grep -qxF "$key" "$HOME/.ssh/authorized_keys" || printf "%s\n" "$key" >> "$HOME/.ssh/authorized_keys"; chmod 700 "$HOME/.ssh"; chmod 600 "$HOME/.ssh/authorized_keys"'
    foreach ($target in @(
        @{ Host = $MachineAHost; User = $MachineAUser; Port = $MachineAPort },
        @{ Host = $MachineBHost; User = $MachineBUser; Port = $MachineBPort }
    )) {
        Write-Host "Installing the admin public key on $($target.Host); verify the displayed host fingerprint before accepting it."
        $publicKey | & ssh.exe -p $target.Port "$($target.User)@$($target.Host)" $remoteInstall
        if ($LASTEXITCODE -ne 0) { throw "Public-key installation failed on $($target.Host)." }
    }
    Write-Host "Public key installed. Test: ssh kevinbellm-a  and  ssh kevinbellm-b"
} else {
    Write-Host "Public key: $IdentityFile.pub"
    Write-Host "Rerun with -InstallPublicKey while password login is still enabled, and verify host fingerprints physically."
}

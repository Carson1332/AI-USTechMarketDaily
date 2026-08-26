<#
.SYNOPSIS
    Refresh data/econ_calendar.json from the local Futu OpenD and push it.

.DESCRIPTION
    The digest runs on a GitHub-hosted runner, which cannot reach OpenD on this machine.
    This script closes that gap locally: pull the forward macro window, commit it, push it.
    The next cloud digest run then reads the committed file.

    Run it from Task Scheduler rather than a self-hosted GitHub runner. This repository is
    public, and a self-hosted runner would let anyone's pull request execute code on this
    machine, which has an authenticated Futu trading gateway listening on localhost.

    Exits non-zero on failure so Task Scheduler shows the run as failed.

    NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads a .ps1 without a BOM as
    ANSI, so non-ASCII characters here become parse errors.

.PARAMETER Days
    Forward window to publish. Default 21.

.PARAMETER NoPush
    Commit but do not push. Use for a dry run.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\refresh_econ_calendar.ps1 -NoPush
#>
param(
    [int]$Days = 21,
    [switch]$NoPush
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Fail($msg) { Write-Error $msg; exit 1 }

# OpenD must be up; without it the publisher would just throw a connection error.
$conn = Test-NetConnection -ComputerName 127.0.0.1 -Port 11111 -WarningAction SilentlyContinue
if (-not $conn.TcpTestSucceeded) {
    Fail "OpenD is not listening on 127.0.0.1:11111. Start FutuOpenD and log in first."
}

$stamp = Get-Date -Format s
Write-Output "[$stamp] refreshing macro calendar, window $Days days"

python tools/publish_econ_calendar.py --days $Days
if ($LASTEXITCODE -ne 0) { Fail "publish_econ_calendar.py exited $LASTEXITCODE" }

# Nothing to do if the forward window produced an identical file.
$changed = git status --porcelain -- data/econ_calendar.json
if (-not $changed) {
    Write-Output "calendar unchanged, nothing to commit"
    exit 0
}

git add data/econ_calendar.json
if ($LASTEXITCODE -ne 0) { Fail "git add failed" }

git commit -m "econ calendar: refresh $(Get-Date -Format yyyy-MM-dd)"
if ($LASTEXITCODE -ne 0) { Fail "git commit failed" }

if ($NoPush) {
    Write-Output "committed; -NoPush set, so not pushing"
    exit 0
}

git push
if ($LASTEXITCODE -ne 0) { Fail "git push failed, check stored credentials" }

Write-Output "[$(Get-Date -Format s)] pushed; next cloud digest will pick it up"

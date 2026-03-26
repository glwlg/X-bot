param(
    [string]$TaskName = "Ikaros",
    [string]$Runner = "scripts/run_ikaros.ps1",
    [ValidateSet("Logon", "Startup")] [string]$TriggerMode = "Logon",
    [switch]$PrintOnly,
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

function Resolve-RunnerPath {
    param([string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }

    return [System.IO.Path]::GetFullPath((Join-Path $ProjectDir $PathValue))
}

function Resolve-ShellPath {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwsh) {
        return $pwsh.Source
    }

    $powershell = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if ($powershell) {
        return $powershell.Source
    }

    throw "Failed to locate pwsh or powershell.exe."
}

function Quote-TaskArgument {
    param([string]$Value)

    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }

    return $Value
}

$RunnerPath = Resolve-RunnerPath $Runner
if (-not (Test-Path $RunnerPath)) {
    throw "Runner not found: $RunnerPath"
}

$ShellPath = Resolve-ShellPath
$ActionArguments = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $RunnerPath
) | ForEach-Object { Quote-TaskArgument $_ }

$Action = New-ScheduledTaskAction -Execute $ShellPath -Argument ($ActionArguments -join " ")

if ($TriggerMode -eq "Startup") {
    $Trigger = New-ScheduledTaskTrigger -AtStartup
    $Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
}
else {
    $CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
    $Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
}

$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Description = "Ikaros task for $RunnerPath"

if ($PrintOnly) {
    Write-Host "TaskName: $TaskName"
    Write-Host "Runner:   $RunnerPath"
    Write-Host "Trigger:  $TriggerMode"
    Write-Host "Shell:    $ShellPath"
    Write-Host "Args:     $($ActionArguments -join ' ')"
    exit 0
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description $Description `
    -Force | Out-Null

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
}

Write-Host "Installed scheduled task: $TaskName"
if ($NoStart) {
    Write-Host "Next: Start-ScheduledTask -TaskName `"$TaskName`""
}
else {
    Write-Host "Status: Get-ScheduledTask -TaskName `"$TaskName`" | Get-ScheduledTaskInfo"
}

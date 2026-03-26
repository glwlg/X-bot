param(
    [switch]$Install
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$WebDir = Join-Path $ProjectDir "src\platforms\web"
$TargetDir = Join-Path $ProjectDir "src\api\static\dist"

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

Require-Command npm

Push-Location $WebDir
try {
    $NodeModulesDir = Join-Path $WebDir "node_modules"
    $PackageLockPath = Join-Path $WebDir "package-lock.json"

    if ($Install -or -not (Test-Path $NodeModulesDir)) {
        if (Test-Path $PackageLockPath) {
            & npm ci
        }
        else {
            & npm install
        }

        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    & npm run build
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}

Write-Host "Built frontend assets into $TargetDir"

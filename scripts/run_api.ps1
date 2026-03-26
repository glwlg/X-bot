param(
    [string]$Host = "0.0.0.0",
    [int]$Port = 8764,
    [switch]$SkipBuild,
    [switch]$Reload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$BuildScript = Join-Path $ScriptDir "build_web.ps1"

function Resolve-UvBin {
    if ($env:IKAROS_UV_BIN) {
        return $env:IKAROS_UV_BIN
    }

    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCommand) {
        return $uvCommand.Source
    }

    $defaultUv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $defaultUv) {
        return $defaultUv
    }

    throw "Failed to locate uv. Install uv or set IKAROS_UV_BIN."
}

if (-not $SkipBuild) {
    & $BuildScript
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$env:PYTHONUNBUFFERED = if ($env:PYTHONUNBUFFERED) { $env:PYTHONUNBUFFERED } else { "1" }
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$ProjectDir\src;$($env:PYTHONPATH)" } else { "$ProjectDir\src" }

$UvBin = Resolve-UvBin
$Arguments = @(
    "run",
    "python",
    "-m",
    "uvicorn",
    "--app-dir",
    (Join-Path $ProjectDir "src"),
    "api.main:app",
    "--host",
    $Host,
    "--port",
    "$Port"
)

if ($Reload) {
    $Arguments += "--reload"
}

Push-Location $ProjectDir
try {
    & $UvBin @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

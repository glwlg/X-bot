Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

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

$env:PYTHONUNBUFFERED = if ($env:PYTHONUNBUFFERED) { $env:PYTHONUNBUFFERED } else { "1" }

$UvBin = Resolve-UvBin
Push-Location $ProjectDir
try {
    & $UvBin run python "src/main.py"
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

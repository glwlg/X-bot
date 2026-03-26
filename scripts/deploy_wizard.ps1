param(
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$EnvFile = Join-Path $ProjectDir ".env"
$EnvTemplate = Join-Path $ProjectDir ".env.example"
$ModelsFile = Join-Path $ProjectDir "config\models.json"
$ModelsTemplate = Join-Path $ProjectDir "config\models.example.json"
$LogDir = Join-Path $ProjectDir "data\logs"
$RunDir = Join-Path $ProjectDir "data\run"
$ApiPort = 8764
$ComposeFile = Join-Path $ProjectDir "docker-compose.yml"

if ($Help) {
    Write-Host "Usage:"
    Write-Host "  .\scripts\deploy_wizard.ps1"
    Write-Host
    Write-Host "Description:"
    Write-Host "  Interactive deployment wizard for Windows."
    Write-Host "  It can initialize .env / config\models.json, optionally configure"
    Write-Host "  Primary / Routing provider settings, and deploy ikaros + ikaros-api."
    exit 0
}

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message"
}

function Write-WarnLine {
    param([string]$Message)
    Write-Warning $Message
}

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

function Ensure-SeedFile {
    param(
        [string]$Target,
        [string]$Template
    )

    if (Test-Path $Target) {
        return
    }
    if (-not (Test-Path $Template)) {
        throw "Template file not found: $Template"
    }
    $parent = Split-Path -Parent $Target
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Copy-Item $Template $Target
    Write-Info "Initialized $(Split-Path -Leaf $Target) from template."
}

function Prompt-Choice {
    param(
        [string]$Prompt,
        [object[]]$Options,
        [int]$DefaultIndex = 1
    )

    Write-Host
    Write-Host $Prompt
    for ($i = 0; $i -lt $Options.Count; $i++) {
        Write-Host ("  {0}) {1}" -f ($i + 1), $Options[$i].label)
    }

    while ($true) {
        $answer = Read-Host "请选择 [$DefaultIndex]"
        if ([string]::IsNullOrWhiteSpace($answer)) {
            $answer = "$DefaultIndex"
        }
        if ($answer -match '^\d+$') {
            $index = [int]$answer
            if ($index -ge 1 -and $index -le $Options.Count) {
                return $Options[$index - 1].value
            }
        }
        Write-Host "请输入有效的序号。"
    }
}

function Prompt-YesNo {
    param(
        [string]$Prompt,
        [bool]$DefaultYes = $true
    )

    $hint = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
    while ($true) {
        $answer = Read-Host "$Prompt $hint"
        if ([string]::IsNullOrWhiteSpace($answer)) {
            return $DefaultYes
        }
        switch ($answer.ToLowerInvariant()) {
            "y" { return $true }
            "yes" { return $true }
            "n" { return $false }
            "no" { return $false }
            default { Write-Host "Please answer yes or no." }
        }
    }
}

function Prompt-Text {
    param(
        [string]$Prompt,
        [string]$Default = "",
        [switch]$Required
    )

    while ($true) {
        $fullPrompt = if ([string]::IsNullOrWhiteSpace($Default)) { $Prompt } else { "$Prompt [$Default]" }
        $value = Read-Host $fullPrompt
        if ([string]::IsNullOrWhiteSpace($value)) {
            $value = $Default
        }
        if (-not $Required -or -not [string]::IsNullOrWhiteSpace($value)) {
            return [string]$value
        }
        Write-Host "不能为空。"
    }
}

function Prompt-Secret {
    param(
        [string]$Prompt,
        [string]$Default = ""
    )

    while ($true) {
        if ([string]::IsNullOrWhiteSpace($Default)) {
            $secure = Read-Host $Prompt -AsSecureString
        }
        else {
            $secure = Read-Host "$Prompt [已配置，回车保持不变]" -AsSecureString
        }
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }

        if ([string]::IsNullOrWhiteSpace($plain)) {
            $plain = $Default
        }
        if (-not [string]::IsNullOrWhiteSpace($plain)) {
            return [string]$plain
        }
        Write-Host "不能为空。"
    }
}

function Load-ModelsJson {
    if (-not (Test-Path $ModelsFile)) {
        return [pscustomobject]@{}
    }
    $raw = Get-Content -Path $ModelsFile -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return [pscustomobject]@{}
    }
    $parsed = $raw | ConvertFrom-Json -Depth 100
    if ($null -eq $parsed) {
        return [pscustomobject]@{}
    }
    return $parsed
}

function Save-ModelsJson {
    param([object]$Data)
    $json = $Data | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText($ModelsFile, ($json + [Environment]::NewLine), [System.Text.Encoding]::UTF8)
}

function Ensure-NoteProperty {
    param(
        [object]$Object,
        [string]$Name,
        $Value
    )
    if ($null -eq $Object.PSObject.Properties[$Name]) {
        $Object | Add-Member -MemberType NoteProperty -Name $Name -Value $Value
    }
}

function Get-RoleBinding {
    param(
        [object]$Data,
        [string]$Role
    )

    if ($null -eq $Data -or $null -eq $Data.model) {
        return ""
    }
    $value = $Data.model.PSObject.Properties[$Role]
    if ($null -eq $value) {
        return ""
    }
    return [string]$value.Value
}

function Get-RoleField {
    param(
        [object]$Data,
        [string]$Role,
        [string]$Field
    )

    $modelKey = Get-RoleBinding -Data $Data -Role $Role
    if ([string]::IsNullOrWhiteSpace($modelKey)) {
        return ""
    }

    $parts = $modelKey.Split("/", 2)
    $providerName = if ($parts.Length -ge 1) { $parts[0] } else { "" }
    $modelId = if ($parts.Length -eq 2) { $parts[1] } else { "" }
    $provider = $null
    if ($Data.providers -and $Data.providers.PSObject.Properties[$providerName]) {
        $provider = $Data.providers.PSObject.Properties[$providerName].Value
    }
    $targetModel = $null
    if ($provider -and $provider.models) {
        foreach ($item in $provider.models) {
            if ($item.id -eq $modelId) {
                $targetModel = $item
                break
            }
        }
    }

    switch ($Field) {
        "provider_name" { return $providerName }
        "model_id" { return $modelId }
        "base_url" {
            if ($provider) {
                return [string]$provider.baseUrl
            }
            return ""
        }
        "api_key" {
            if ($provider) {
                return [string]$provider.apiKey
            }
            return ""
        }
        "api_style" {
            if ($provider) {
                return [string]$provider.api
            }
            return ""
        }
        default { return "" }
    }
}

function Get-ProviderField {
    param(
        [object]$Data,
        [string]$ProviderName,
        [string]$Field
    )

    if ([string]::IsNullOrWhiteSpace($ProviderName) -or -not $Data.providers) {
        return ""
    }
    $property = $Data.providers.PSObject.Properties[$ProviderName]
    if ($null -eq $property) {
        return ""
    }
    $provider = $property.Value
    switch ($Field) {
        "base_url" { return [string]$provider.baseUrl }
        "api_key" { return [string]$provider.apiKey }
        "api_style" { return [string]$provider.api }
        default { return "" }
    }
}

function Get-AvailableModelKeys {
    param([object]$Data)

    $keys = New-Object System.Collections.Generic.List[string]

    if ($Data.providers) {
        foreach ($providerProp in $Data.providers.PSObject.Properties) {
            $providerName = $providerProp.Name
            $provider = $providerProp.Value
            if ($provider -and $provider.models) {
                foreach ($model in $provider.models) {
                    if ($model.id) {
                        $keys.Add("$providerName/$($model.id)")
                    }
                }
            }
        }
    }

    if ($Data.models) {
        foreach ($roleProp in $Data.models.PSObject.Properties) {
            $pool = $roleProp.Value
            if ($pool -is [System.Collections.IDictionary] -or $pool -is [pscustomobject]) {
                foreach ($item in $pool.PSObject.Properties) {
                    if (-not [string]::IsNullOrWhiteSpace([string]$item.Name)) {
                        $keys.Add([string]$item.Name)
                    }
                }
            }
            elseif ($pool -is [System.Collections.IEnumerable]) {
                foreach ($item in $pool) {
                    if (-not [string]::IsNullOrWhiteSpace([string]$item)) {
                        $keys.Add([string]$item)
                    }
                }
            }
        }
    }

    if ($Data.model) {
        foreach ($bindingProp in $Data.model.PSObject.Properties) {
            if (-not [string]::IsNullOrWhiteSpace([string]$bindingProp.Value)) {
                $keys.Add([string]$bindingProp.Value)
            }
        }
    }

    return $keys | Select-Object -Unique
}

function Show-ModelReference {
    param([object]$Data)

    $keys = @(Get-AvailableModelKeys -Data $Data)
    Write-Host
    Write-Host "当前 models.json 里的已知模型键："
    if ($keys.Count -eq 0) {
        Write-Host "  (当前 models.json 中还没有可枚举的模型键，将改为手动输入)"
        return
    }
    for ($i = 0; $i -lt $keys.Count; $i++) {
        Write-Host ("  {0}) {1}" -f ($i + 1), $keys[$i])
    }
}

function Configure-RoleModel {
    param(
        [object]$Data,
        [string]$Role,
        [string]$RoleLabel
    )

    Show-ModelReference -Data $Data

    $defaultProvider = Get-RoleField -Data $Data -Role $Role -Field "provider_name"
    $defaultModelId = Get-RoleField -Data $Data -Role $Role -Field "model_id"
    $defaultBaseUrl = Get-RoleField -Data $Data -Role $Role -Field "base_url"
    $defaultApiKey = Get-RoleField -Data $Data -Role $Role -Field "api_key"
    $defaultApiStyle = Get-RoleField -Data $Data -Role $Role -Field "api_style"

    $providerName = Prompt-Text -Prompt "请输入 $RoleLabel provider 名称" -Default $defaultProvider -Required
    $modelId = Prompt-Text -Prompt "请输入 $RoleLabel model_id（最终 key 为 $providerName/...）" -Default $defaultModelId -Required

    $suggestedBaseUrl = Get-ProviderField -Data $Data -ProviderName $providerName -Field "base_url"
    if ([string]::IsNullOrWhiteSpace($suggestedBaseUrl)) {
        $suggestedBaseUrl = $defaultBaseUrl
    }
    $baseUrl = Prompt-Text -Prompt "请输入 $RoleLabel baseUrl" -Default $suggestedBaseUrl -Required

    $suggestedApiKey = Get-ProviderField -Data $Data -ProviderName $providerName -Field "api_key"
    if ([string]::IsNullOrWhiteSpace($suggestedApiKey)) {
        $suggestedApiKey = $defaultApiKey
    }
    $apiKey = Prompt-Secret -Prompt "请输入 $RoleLabel apiKey" -Default $suggestedApiKey

    $suggestedApiStyle = Get-ProviderField -Data $Data -ProviderName $providerName -Field "api_style"
    if ([string]::IsNullOrWhiteSpace($suggestedApiStyle)) {
        $suggestedApiStyle = if ([string]::IsNullOrWhiteSpace($defaultApiStyle)) { "openai-completions" } else { $defaultApiStyle }
    }
    $apiStyle = Prompt-Text -Prompt "请输入 $RoleLabel API 风格" -Default $suggestedApiStyle -Required

    return [pscustomobject]@{
        ProviderName = $providerName
        ModelId = $modelId
        BaseUrl = $baseUrl
        ApiKey = $apiKey
        ApiStyle = $apiStyle
        ModelKey = "$providerName/$modelId"
    }
}

function Upsert-ProviderModel {
    param(
        [object]$Data,
        [string]$Role,
        [pscustomobject]$Config
    )

    Ensure-NoteProperty -Object $Data -Name "model" -Value ([pscustomobject]@{})
    Ensure-NoteProperty -Object $Data -Name "models" -Value ([pscustomobject]@{})
    Ensure-NoteProperty -Object $Data -Name "providers" -Value ([pscustomobject]@{})
    Ensure-NoteProperty -Object $Data -Name "mode" -Value "merge"

    $providerProp = $Data.providers.PSObject.Properties[$Config.ProviderName]
    if ($null -eq $providerProp) {
        $provider = [pscustomobject]@{
            baseUrl = $Config.BaseUrl
            apiKey = $Config.ApiKey
            api = $Config.ApiStyle
            models = @()
        }
        $Data.providers | Add-Member -MemberType NoteProperty -Name $Config.ProviderName -Value $provider
    }
    else {
        $provider = $providerProp.Value
        if ($null -eq $provider.models) {
            $provider | Add-Member -MemberType NoteProperty -Name "models" -Value @() -Force
        }
        $provider.baseUrl = $Config.BaseUrl
        $provider.apiKey = $Config.ApiKey
        $provider.api = $Config.ApiStyle
    }

    $existingModel = $null
    foreach ($item in $provider.models) {
        if ($item.id -eq $Config.ModelId) {
            $existingModel = $item
            break
        }
    }

    if ($null -eq $existingModel) {
        $defaultInputs = if ($Role -eq "primary") { @("text", "image", "voice") } else { @("text") }
        $existingModel = [pscustomobject]@{
            id = $Config.ModelId
            name = $Config.ModelId
            reasoning = ($Role -eq "primary")
            input = $defaultInputs
            cost = @{
                input = 0
                output = 0
                cacheRead = 0
                cacheWrite = 0
            }
            contextWindow = 1000000
            maxTokens = 65536
        }
        $provider.models += $existingModel
    }

    $Data.model | Add-Member -MemberType NoteProperty -Name $Role -Value $Config.ModelKey -Force

    $rolePoolProp = $Data.models.PSObject.Properties[$Role]
    if ($null -eq $rolePoolProp -or $null -eq $rolePoolProp.Value) {
        $rolePool = [pscustomobject]@{}
        $Data.models | Add-Member -MemberType NoteProperty -Name $Role -Value $rolePool -Force
    }
    else {
        $rolePool = $rolePoolProp.Value
    }

    if ($null -eq $rolePool.PSObject.Properties[$Config.ModelKey]) {
        $rolePool | Add-Member -MemberType NoteProperty -Name $Config.ModelKey -Value @{}
    }
}

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

function Ensure-UvSync {
    $uvBin = Resolve-UvBin
    Write-Info "Running uv sync ..."
    Push-Location $ProjectDir
    try {
        & $uvBin sync
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    finally {
        Pop-Location
    }
}

function Ensure-WebBuild {
    Write-Info "Building Web frontend ..."
    $script = Join-Path $ScriptDir "build_web.ps1"
    & $script -Install
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
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

function Stop-BackgroundIfRequested {
    param(
        [string]$Name,
        [string]$PidFile
    )

    if (-not (Test-Path $PidFile)) {
        return $true
    }

    $raw = (Get-Content -Path $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not ($raw -as [int])) {
        Remove-Item $PidFile -ErrorAction SilentlyContinue
        return $true
    }

    $pid = [int]$raw
    $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Remove-Item $PidFile -ErrorAction SilentlyContinue
        return $true
    }

    if (Prompt-YesNo -Prompt "$Name 似乎已经在运行（PID $pid）。是否重启它？" -DefaultYes $true) {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        Remove-Item $PidFile -ErrorAction SilentlyContinue
        return $true
    }

    Write-Info "Keeping existing $Name process."
    return $false
}

function Start-BackgroundService {
    param(
        [string]$Name,
        [string]$PidFile,
        [string]$StdOutFile,
        [string]$StdErrFile,
        [string[]]$RunnerArguments
    )

    New-Item -ItemType Directory -Force -Path $LogDir, $RunDir | Out-Null

    if (-not (Stop-BackgroundIfRequested -Name $Name -PidFile $PidFile)) {
        return
    }

    $shellPath = Resolve-ShellPath
    $process = Start-Process `
        -FilePath $shellPath `
        -ArgumentList $RunnerArguments `
        -WorkingDirectory $ProjectDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdOutFile `
        -RedirectStandardError $StdErrFile `
        -PassThru

    Set-Content -Path $PidFile -Value $process.Id -Encoding UTF8
    Write-Info "Started $Name in background (PID $($process.Id)). Log: $StdOutFile"
}

function Resolve-ComposeCommand {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        try {
            & $docker.Source compose version *> $null
            if ($LASTEXITCODE -eq 0) {
                return @($docker.Source, "compose")
            }
        }
        catch {
        }
    }

    $dockerCompose = Get-Command docker-compose -ErrorAction SilentlyContinue
    if ($dockerCompose) {
        return @($dockerCompose.Source)
    }

    throw "Failed to locate docker compose or docker-compose."
}

function Invoke-ComposeUp {
    param([string[]]$Services)
    $compose = Resolve-ComposeCommand
    $arguments = @()
    if ($compose.Count -gt 1) {
        $arguments += $compose[1..($compose.Count - 1)]
    }
    $arguments += @("-f", $ComposeFile, "up", "--build", "-d")
    $arguments += $Services
    Push-Location $ProjectDir
    try {
        & $compose[0] @arguments
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    finally {
        Pop-Location
    }
}

function Install-WindowsTaskMode {
    param(
        [string]$ServiceKind
    )

    $script = Join-Path $ScriptDir "install_windows_task.ps1"
    if ($ServiceKind -eq "ikaros") {
        & $script -TaskName IkarosCore -Runner "scripts/run_ikaros.ps1"
    }
    else {
        & $script -TaskName IkarosApi -Runner "scripts/run_api.ps1"
    }
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Get-DisplayHost {
    if (Test-Path $EnvFile) {
        foreach ($line in Get-Content -Path $EnvFile -Encoding UTF8) {
            if ($line -match '^\s*SERVER_IP\s*=\s*(.+)\s*$') {
                $value = $Matches[1].Trim().Trim('"').Trim("'")
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    return $value
                }
            }
        }
    }

    try {
        $hostEntry = [System.Net.Dns]::GetHostEntry([System.Net.Dns]::GetHostName())
        foreach ($ip in $hostEntry.AddressList) {
            if ($ip.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and -not $ip.ToString().StartsWith("169.254.")) {
                return $ip.ToString()
            }
        }
    }
    catch {
    }

    return "127.0.0.1"
}

function Print-NextSteps {
    param(
        [string]$DisplayHost,
        [string]$IkarosMode,
        [string]$ApiMode,
        [string]$ModelMode,
        [string]$PrimaryKey,
        [string]$RoutingKey
    )

    Write-Host
    Write-Host "Deployment summary"
    Write-Host "------------------"
    Write-Host "Ikaros Core : $IkarosMode"
    Write-Host "Ikaros API  : $ApiMode"
    Write-Host "Models      : $ModelMode"
    if (-not [string]::IsNullOrWhiteSpace($PrimaryKey)) {
        Write-Host "Primary     : $PrimaryKey"
    }
    if (-not [string]::IsNullOrWhiteSpace($RoutingKey)) {
        Write-Host "Routing     : $RoutingKey"
    }
    Write-Host

    if ($ApiMode -ne "skip") {
        Write-Host "Next:"
        Write-Host "1. Open http://$DisplayHost`:$ApiPort/login"
        Write-Host "2. Complete the first admin bootstrap"
        Write-Host "3. Enter /admin/setup to finish models / SOUL / USER / channels initialization"
        Write-Host
    }
    else {
        Write-Host "API was not deployed in this run, so Web bootstrap is not available yet."
        Write-Host "Deploy ikaros-api later, then open /login to finish initialization."
        Write-Host
    }

    switch ($IkarosMode) {
        "shell_bg" { Write-Host "Core logs: Get-Content -Wait `"$LogDir\ikaros.out.log`"" }
        "scheduled_task" { Write-Host "Core status: Get-ScheduledTask -TaskName `"IkarosCore`" | Get-ScheduledTaskInfo" }
        "compose" { Write-Host "Core logs: docker compose logs -f ikaros" }
    }

    switch ($ApiMode) {
        "shell_bg" { Write-Host "API logs:  Get-Content -Wait `"$LogDir\ikaros-api.out.log`"" }
        "scheduled_task" { Write-Host "API status: Get-ScheduledTask -TaskName `"IkarosApi`" | Get-ScheduledTaskInfo" }
        "compose" { Write-Host "API logs:  docker compose logs -f ikaros-api" }
    }
}

Write-Host "Ikaros Deployment Wizard (Windows)"
Write-Host "=================================="
Write-Host "Project: $ProjectDir"
Write-Host

Ensure-SeedFile -Target $EnvFile -Template $EnvTemplate
Ensure-SeedFile -Target $ModelsFile -Template $ModelsTemplate

$deploymentOptions = @(
    @{ value = "shell_bg"; label = "后台脚本模式（Start-Process 启动）" }
    @{ value = "scheduled_task"; label = "Windows 计划任务" }
    @{ value = "compose"; label = "Docker Compose" }
    @{ value = "skip"; label = "暂不部署" }
)

$modelOptions = @(
    @{ value = "now"; label = "现在写入 config\models.json" }
    @{ value = "later"; label = "稍后到 Web 初始化页配置" }
)

$ikarosMode = Prompt-Choice -Prompt "选择 Ikaros Core 的部署方式" -Options $deploymentOptions -DefaultIndex 1
$apiMode = Prompt-Choice -Prompt "选择 Ikaros API 的部署方式" -Options $deploymentOptions -DefaultIndex 1
$modelMode = Prompt-Choice -Prompt "Primary / Routing 模型现在怎么处理？" -Options $modelOptions -DefaultIndex 2

$primaryKey = ""
$routingKey = ""
$modelsData = Load-ModelsJson

if ($modelMode -eq "now") {
    $primaryConfig = Configure-RoleModel -Data $modelsData -Role "primary" -RoleLabel "Primary"
    $primaryKey = $primaryConfig.ModelKey

    if (Prompt-YesNo -Prompt "Routing 是否复用 Primary 的 provider / baseUrl / apiKey / API 风格？" -DefaultYes $true) {
        Show-ModelReference -Data $modelsData
        $routingModelId = Prompt-Text -Prompt "请输入 Routing model_id（最终 key 为 $($primaryConfig.ProviderName)/...）" -Default (Get-RoleField -Data $modelsData -Role "routing" -Field "model_id") -Required
        $routingConfig = [pscustomobject]@{
            ProviderName = $primaryConfig.ProviderName
            ModelId = $routingModelId
            BaseUrl = $primaryConfig.BaseUrl
            ApiKey = $primaryConfig.ApiKey
            ApiStyle = $primaryConfig.ApiStyle
            ModelKey = "$($primaryConfig.ProviderName)/$routingModelId"
        }
    }
    else {
        $routingConfig = Configure-RoleModel -Data $modelsData -Role "routing" -RoleLabel "Routing"
    }

    $routingKey = $routingConfig.ModelKey
    Upsert-ProviderModel -Data $modelsData -Role "primary" -Config $primaryConfig
    Upsert-ProviderModel -Data $modelsData -Role "routing" -Config $routingConfig
    Save-ModelsJson -Data $modelsData
    Write-Info "Updated provider/baseUrl/apiKey plus model.primary and model.routing in $ModelsFile"
}

$needUvSync = $false
$needWebBuild = $false
$needCompose = $false

switch ($ikarosMode) {
    "shell_bg" { $needUvSync = $true }
    "scheduled_task" { $needUvSync = $true }
    "compose" { $needCompose = $true }
}

switch ($apiMode) {
    "shell_bg" {
        $needUvSync = $true
        $needWebBuild = $true
    }
    "scheduled_task" {
        $needUvSync = $true
        $needWebBuild = $true
    }
    "compose" { $needCompose = $true }
}

if ($needUvSync) {
    Ensure-UvSync
}

if ($needWebBuild) {
    Ensure-WebBuild
}

switch ($ikarosMode) {
    "shell_bg" {
        Start-BackgroundService `
            -Name "ikaros" `
            -PidFile (Join-Path $RunDir "ikaros.pid") `
            -StdOutFile (Join-Path $LogDir "ikaros.out.log") `
            -StdErrFile (Join-Path $LogDir "ikaros.err.log") `
            -RunnerArguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptDir "run_ikaros.ps1"))
    }
    "scheduled_task" {
        Install-WindowsTaskMode -ServiceKind "ikaros"
    }
}

switch ($apiMode) {
    "shell_bg" {
        Start-BackgroundService `
            -Name "ikaros-api" `
            -PidFile (Join-Path $RunDir "ikaros-api.pid") `
            -StdOutFile (Join-Path $LogDir "ikaros-api.out.log") `
            -StdErrFile (Join-Path $LogDir "ikaros-api.err.log") `
            -RunnerArguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptDir "run_api.ps1"), "-SkipBuild", "-Host", "0.0.0.0", "-Port", "$ApiPort")
    }
    "scheduled_task" {
        Install-WindowsTaskMode -ServiceKind "api"
    }
}

if ($needCompose) {
    switch ("$ikarosMode`:$apiMode") {
        "compose:compose" { Invoke-ComposeUp -Services @("ikaros", "ikaros-api") }
        "compose:skip" { Invoke-ComposeUp -Services @("ikaros") }
        "skip:compose" { Invoke-ComposeUp -Services @("ikaros-api") }
        default {
            if ($ikarosMode -eq "compose") {
                Invoke-ComposeUp -Services @("ikaros")
            }
            elseif ($apiMode -eq "compose") {
                Invoke-ComposeUp -Services @("ikaros-api")
            }
        }
    }
}

$displayHost = Get-DisplayHost
Print-NextSteps `
    -DisplayHost $displayHost `
    -IkarosMode $ikarosMode `
    -ApiMode $apiMode `
    -ModelMode $modelMode `
    -PrimaryKey $primaryKey `
    -RoutingKey $routingKey

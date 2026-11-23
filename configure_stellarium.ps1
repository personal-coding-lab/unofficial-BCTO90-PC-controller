$ErrorActionPreference = 'Stop'

$running = Get-Process -Name stellarium -ErrorAction SilentlyContinue
if ($running) {
    throw 'Close Stellarium completely, then run this script again.'
}

$userDir = Join-Path $env:APPDATA 'Stellarium'
$configPath = Join-Path $userDir 'config.ini'
$moduleDir = Join-Path $userDir 'modules\TelescopeControl'
$telescopesPath = Join-Path $moduleDir 'telescopes.json'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Could not find the Stellarium configuration file: $configPath"
}

Copy-Item -LiteralPath $configPath -Destination "$configPath.bcto90-$stamp.bak"
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
if ($config -match '(?m)^\s*TelescopeControl\s*=') {
    $config = [regex]::Replace($config, '(?m)^(\s*TelescopeControl\s*=\s*).+$', '${1}true')
} else {
    throw 'Could not find the TelescopeControl entry in config.ini.'
}
[System.IO.File]::WriteAllText($configPath, $config, [System.Text.UTF8Encoding]::new($false))

New-Item -ItemType Directory -Path $moduleDir -Force | Out-Null
if (Test-Path -LiteralPath $telescopesPath) {
    Copy-Item -LiteralPath $telescopesPath -Destination "$telescopesPath.bcto90-$stamp.bak"
    $settings = Get-Content -LiteralPath $telescopesPath -Raw -Encoding UTF8 | ConvertFrom-Json
} else {
    $settings = [pscustomobject]@{ version = '0.4.1' }
}

$slot = $null
foreach ($candidate in 1..9) {
    $property = $settings.PSObject.Properties[[string]$candidate]
    if ($property -and $property.Value.name -eq 'BCTO90 BLE Bridge') {
        $slot = [string]$candidate
        break
    }
    if (-not $property -and -not $slot) {
        $slot = [string]$candidate
    }
}
if (-not $slot) {
    throw 'All Stellarium telescope slots (1–9) are in use. Free a slot, then run this script again.'
}

$entry = [pscustomobject]@{
    connect_at_startup = $true
    connection = 'remote'
    delay = 500000
    equinox = 'JNow'
    host_name = '127.0.0.1'
    name = 'BCTO90 BLE Bridge'
    tcp_port = 10001
}
$existing = $settings.PSObject.Properties[$slot]
if ($existing) {
    $existing.Value = $entry
} else {
    Add-Member -InputObject $settings -MemberType NoteProperty -Name $slot -Value $entry
}
$json = $settings | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($telescopesPath, $json, [System.Text.UTF8Encoding]::new($false))

Write-Host "Stellarium Telescope Control configured: slot $slot / BCTO90 BLE Bridge / 127.0.0.1:10001"
Write-Host "Backup: $configPath.bcto90-$stamp.bak"

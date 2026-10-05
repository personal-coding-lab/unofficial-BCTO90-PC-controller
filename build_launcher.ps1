$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSEdition -ne 'Desktop') {
    $windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    & $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath
    exit $LASTEXITCODE
}

$sourcePath = Join-Path $PSScriptRoot 'launcher\RunLauncher.cs'
$outputPath = Join-Path $PSScriptRoot 'run.exe'
$temporaryPath = Join-Path $PSScriptRoot 'run.build.exe'
$source = Get-Content -LiteralPath $sourcePath -Raw -Encoding UTF8

try {
    Add-Type `
        -TypeDefinition $source `
        -ReferencedAssemblies 'System.Windows.Forms' `
        -OutputAssembly $temporaryPath `
        -OutputType WindowsApplication
    Move-Item -LiteralPath $temporaryPath -Destination $outputPath -Force
    Write-Host "Launcher built: $outputPath"
} finally {
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}

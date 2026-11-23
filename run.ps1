$ErrorActionPreference = 'Stop'

try {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
    $venv = Join-Path $root '.venv'
    $python = Join-Path $venv 'Scripts\python.exe'

    if (-not (Test-Path -LiteralPath $python)) {
        $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
        $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($systemPython) {
            & $systemPython.Source -c "import sys; raise SystemExit(sys.version_info < (3, 10))"
            if ($LASTEXITCODE -ne 0) {
                throw 'Python 3.10 or later is required.'
            }
            & $systemPython.Source -m venv $venv
        } elseif ($pyLauncher) {
            & $pyLauncher.Source -3 -m venv $venv
        } else {
            throw 'Could not find Python 3.10 or later. Install Python for Windows from python.org first.'
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) {
            throw 'Could not create the Python virtual environment.'
        }
    }

    & $python -c "import sys; raise SystemExit(sys.version_info < (3, 10))"
    if ($LASTEXITCODE -ne 0) {
        throw 'The Python version in the existing .venv is older than 3.10. Delete the .venv folder and run this again.'
    }

    & $python -c "import tkinter; from importlib.metadata import version; raise SystemExit(version('bleak') != '3.0.2')"
    if ($LASTEXITCODE -ne 0) {
        & $python -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements.txt')
        if ($LASTEXITCODE -ne 0) {
            throw 'Could not install the required Python packages. Check the internet connection and firewall.'
        }
    }

    & $python (Join-Path $root 'bcto90_gui.py')
    if ($LASTEXITCODE -ne 0) {
        throw "The program exited with error code $LASTEXITCODE."
    }
} catch {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        $_.Exception.Message,
        'BCTO90 Controller launch error',
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
    exit 1
}

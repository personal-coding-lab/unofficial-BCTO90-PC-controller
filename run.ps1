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
                throw 'Python 3.10 이상이 필요합니다.'
            }
            & $systemPython.Source -m venv $venv
        } elseif ($pyLauncher) {
            & $pyLauncher.Source -3 -m venv $venv
        } else {
            throw 'Python 3.10 이상을 찾지 못했습니다. python.org의 Windows용 Python을 먼저 설치하세요.'
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) {
            throw 'Python 가상환경을 만들지 못했습니다.'
        }
    }

    & $python -c "import sys; raise SystemExit(sys.version_info < (3, 10))"
    if ($LASTEXITCODE -ne 0) {
        throw '기존 .venv의 Python이 3.10 미만입니다. .venv 폴더를 삭제한 뒤 다시 실행하세요.'
    }

    & $python -c "import tkinter; from importlib.metadata import version; raise SystemExit(version('bleak') != '3.0.2')"
    if ($LASTEXITCODE -ne 0) {
        & $python -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements.txt')
        if ($LASTEXITCODE -ne 0) {
            throw '필수 Python 패키지를 설치하지 못했습니다. 인터넷 연결과 방화벽을 확인하세요.'
        }
    }

    & $python (Join-Path $root 'bcto90_gui.py')
    if ($LASTEXITCODE -ne 0) {
        throw "프로그램이 오류 코드 $LASTEXITCODE 로 종료되었습니다."
    }
} catch {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        $_.Exception.Message,
        'BCTO90 Controller 실행 오류',
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
    exit 1
}

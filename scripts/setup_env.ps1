param([string]$Python)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $projectPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
    $checkPython = @'
import pathlib, sys
base = pathlib.Path(sys.base_prefix)
assert sys.version_info[:2] == (3, 12), 'Use standalone CPython 3.12'
assert 'anaconda' not in sys.version.lower() and 'conda' not in str(base).lower() and not (base / 'conda-meta').exists(), 'Conda-backed Python is not supported in this project'
print(sys.executable)
'@
    if (-not (Test-Path -LiteralPath $projectPython)) {
        if (Test-Path -LiteralPath '.venv') {
            throw 'An incomplete .venv exists. Archive it before creating a replacement.'
        }
        if (-not $Python) {
            if (Get-Command py -ErrorAction SilentlyContinue) {
                $Python = & py -3.12 -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -ne 0) { $Python = $null }
            }
            if (-not $Python -and (Get-Command uv -ErrorAction SilentlyContinue)) {
                $Python = & uv --cache-dir .tmp/uv-cache python find --managed-python 3.12 2>$null
                if ($LASTEXITCODE -ne 0) { $Python = $null }
            }
        }
        if (-not $Python) {
            throw 'Install standalone CPython 3.12, then run ./scripts/setup_env.ps1 -Python <path-to-python.exe>.'
        }
        & $Python -c $checkPython
        if ($LASTEXITCODE -ne 0) { throw 'The base Python failed environment checks.' }
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Creating .venv failed.' }
    }
    & $projectPython -c $checkPython
    if ($LASTEXITCODE -ne 0) { throw 'The existing .venv failed environment checks. Archive it and recreate it.' }
    & $projectPython -m pip install --disable-pip-version-check -r requirements/locks/windows-py312.txt
    if ($LASTEXITCODE -ne 0) { throw 'Installing project dependencies failed.' }
    & $projectPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    Write-Output "Project environment ready: $projectPython"
} finally {
    Pop-Location
}

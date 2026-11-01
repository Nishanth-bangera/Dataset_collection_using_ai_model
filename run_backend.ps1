$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectPython = Join-Path $projectRoot "venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $projectPython)) {
    throw "Project virtual environment not found at '$projectPython'. Create it and install backend requirements first."
}

Push-Location $projectRoot
try {
    & $projectPython -m uvicorn backend.main:app --reload --app-dir $projectRoot
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

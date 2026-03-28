$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
$envFile = Join-Path $projectRoot ".env"

if (-not (Test-Path $python)) {
  throw "Python virtual environment not found: $python"
}

if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
      return
    }
    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
      [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
    }
  }
}

Set-Location $projectRoot

& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8765

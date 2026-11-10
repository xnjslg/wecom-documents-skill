$ErrorActionPreference = 'Stop'
$wecomPython = $env:WECOM_DOCUMENT_PYTHON
if (-not $wecomPython -and $env:VIRTUAL_ENV) {
    $wecomPython = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
}
if (-not $wecomPython -and $env:CONDA_PREFIX) {
    $wecomPython = Join-Path $env:CONDA_PREFIX 'python.exe'
}
if (-not $wecomPython) {
    $wecomPython = Join-Path $env:USERPROFILE 'miniconda3\envs\wecom-documents\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $wecomPython -PathType Leaf)) {
    throw 'Activate a Windows Python 3.11+ virtual environment or set WECOM_DOCUMENT_PYTHON to its python.exe. See references/client-transport.md.'
}
& $wecomPython (Join-Path $PSScriptRoot 'document.py') @args
exit $LASTEXITCODE

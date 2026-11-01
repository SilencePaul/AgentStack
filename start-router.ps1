param()
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$env:PYTHONUTF8='1'
python -m router.cli serve

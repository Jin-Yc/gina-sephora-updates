$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' 'packaging\release_tools.py'
if ($LASTEXITCODE -ne 0) { throw 'Update package preparation failed. Check update-config.json.' }
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm 'packaging\GinaSephora.spec'
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
Write-Output 'Ready: dist\GinaSephora.exe'

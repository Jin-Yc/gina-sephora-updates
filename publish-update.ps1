param(
    [switch]$Publish
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python 'packaging\release_tools.py'
if ($LASTEXITCODE -ne 0) { throw 'Update package creation failed.' }
$version = & $python -c 'from version import APP_VERSION; print(APP_VERSION)'
if ($LASTEXITCODE -ne 0) { throw 'Cannot read version.py.' }
$repository = (Get-Content -LiteralPath 'update-config.json' -Raw | ConvertFrom-Json).repository
$releaseDirectory = Join-Path $PSScriptRoot "dist\updates\$version"
$archive = Join-Path $releaseDirectory "GinaSephora-app-$version.zip"
$manifest = Join-Path $releaseDirectory 'update.json'

if ($Publish) {
    & gh auth status
    if ($LASTEXITCODE -ne 0) { throw 'Run gh auth login first, or upload the ZIP and update.json using the GitHub website.' }
    $notes = Join-Path $releaseDirectory 'release-notes.md'
    "Business code update $version. The desktop launcher checks this release on the next start." | Set-Content -LiteralPath $notes -Encoding utf8
    # Publish only after both assets are uploaded; never expose a partial update.
    & gh release create "v$version" $archive $manifest --repo $repository --title "v$version" --notes-file $notes --draft
    if ($LASTEXITCODE -ne 0) { throw 'Draft release creation failed. Existing releases are not overwritten.' }
    & gh release edit "v$version" --repo $repository --draft=false --latest
    if ($LASTEXITCODE -ne 0) { throw 'Release is still a draft; publish it after checking both assets.' }
    Write-Output "Published: https://github.com/$repository/releases/tag/v$version"
} else {
    Write-Output "Upload both files at https://github.com/$repository/releases/new"
    Write-Output "Tag: v$version (publish as latest, not prerelease)"
    Write-Output $archive
    Write-Output $manifest
}

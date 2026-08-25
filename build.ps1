param([string]$Version = "0.2.3-r1")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Join-Path $Root "KazumiLite"
$Output = Join-Path $Root "output"
$Stage = Join-Path ([IO.Path]::GetTempPath()) ("kazumilite-" + [guid]::NewGuid())
$StageApp = Join-Path $Stage "KazumiLite"
$Archive = Join-Path $Output ("KazumiLite-Jacaranda-Daily-$Version.muxapp")

New-Item -ItemType Directory -Force -Path $Output, $StageApp | Out-Null
Get-ChildItem -LiteralPath $Source -Force | Copy-Item -Destination $StageApp -Recurse -Force

Get-ChildItem -LiteralPath $StageApp -Recurse -Force | Where-Object {
    $_.Name -eq "__pycache__" -or $_.Extension -eq ".pyc" -or
    $_.Name -in @("log.txt", "mpv.log", "diagnostics.txt", "state.json")
} | Remove-Item -Recurse -Force

if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory(
    $Stage,
    $Archive,
    [IO.Compression.CompressionLevel]::Optimal,
    $false
)
Remove-Item -LiteralPath $Stage -Recurse -Force

Get-FileHash -Algorithm SHA256 -LiteralPath $Archive

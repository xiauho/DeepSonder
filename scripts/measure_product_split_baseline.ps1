param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Get-RelativePath([string]$Path) {
    return [IO.Path]::GetRelativePath($ProjectRoot, $Path).Replace("\", "/")
}

function Measure-File([string]$RelativePath) {
    $path = Join-Path $ProjectRoot $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return [ordered]@{ path = $RelativePath.Replace("\", "/"); available = $false }
    }
    $item = Get-Item -LiteralPath $path
    return [ordered]@{
        path = Get-RelativePath $item.FullName
        available = $true
        bytes = $item.Length
        sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        modified_at = $item.LastWriteTime.ToString("o")
    }
}

function Measure-Directory([string]$RelativePath) {
    $path = Join-Path $ProjectRoot $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        return [ordered]@{ path = $RelativePath.Replace("\", "/"); available = $false }
    }
    $files = @(Get-ChildItem -LiteralPath $path -File -Recurse)
    $bytes = [long](($files | Measure-Object Length -Sum).Sum)
    return [ordered]@{
        path = Get-RelativePath (Resolve-Path -LiteralPath $path).Path
        available = $true
        bytes = $bytes
        file_count = $files.Count
    }
}

$targetRoot = "D:\GitHub-store\DeepSonder"
$targetItems = if (Test-Path -LiteralPath $targetRoot -PathType Container) {
    @(Get-ChildItem -LiteralPath $targetRoot -Force)
} else {
    @()
}

$result = [ordered]@{
    schema_version = 1
    measured_at = (Get-Date).ToString("o")
    repository_commit = (& git -C $ProjectRoot rev-parse HEAD).Trim()
    note = "Observed pre-split artifacts may come from different local rehearsal runs; use them only as size baselines."
    pyside6 = [ordered]@{
        portable_zip = Measure-File "dist\release\Novalist-v2.1.0-beta-windows-x64.zip"
        executable = Measure-File "dist\Novalist\Novalist.exe"
        unpacked = Measure-Directory "dist\Novalist"
    }
    electron = [ordered]@{
        installer = Measure-File "electron\release\Novalist-v2.1.0-beta-windows-x64-setup.exe"
        portable_zip = Measure-File "electron\release\Novalist-v2.1.0-beta-windows-x64.zip"
        executable = Measure-File "electron\release\win-unpacked\DeepSonder.exe"
        sidecar = Measure-File "electron\release\win-unpacked\resources\sidecar\NovalistSidecar.exe"
        unpacked = Measure-Directory "electron\release\win-unpacked"
    }
    electron_target = [ordered]@{
        path = $targetRoot
        exists = Test-Path -LiteralPath $targetRoot -PathType Container
        item_count = $targetItems.Count
        empty = (Test-Path -LiteralPath $targetRoot -PathType Container) -and $targetItems.Count -eq 0
    }
}

$json = $result | ConvertTo-Json -Depth 8
if ($OutputPath) {
    $resolvedOutput = if ([IO.Path]::IsPathRooted($OutputPath)) {
        [IO.Path]::GetFullPath($OutputPath)
    } else {
        [IO.Path]::GetFullPath((Join-Path $ProjectRoot $OutputPath))
    }
    $parent = Split-Path -Parent $resolvedOutput
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Set-Content -LiteralPath $resolvedOutput -Value $json -Encoding utf8
}
$json

[CmdletBinding()]
param(
    [string]$PythonExecutable = "",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}
if (Test-Path -LiteralPath $PythonExecutable -PathType Leaf) {
    $PythonExecutable = (Resolve-Path -LiteralPath $PythonExecutable).Path
}
else {
    $PythonCommand = Get-Command $PythonExecutable -ErrorAction SilentlyContinue
    if ($null -eq $PythonCommand -or -not $PythonCommand.Source) {
        throw "未找到构建用 Python：$PythonExecutable"
    }
    $PythonExecutable = $PythonCommand.Source
}

$Version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "VERSION 格式无效：$Version"
}
$PythonVersion = (& $PythonExecutable -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($LASTEXITCODE -ne 0 -or $PythonVersion -ne "3.12") {
    throw "Windows 包必须使用 Python 3.12，当前为：$PythonVersion"
}

$BuildRoot = Join-Path $ProjectRoot "build\pyinstaller-pyside6"
$DistRoot = Join-Path $ProjectRoot "dist"
$BundleRoot = Join-Path $DistRoot "DeepSonder-PySide6"
$ReleaseRoot = Join-Path $DistRoot "release-pyside6"
foreach ($Target in @($BuildRoot, $DistRoot, $BundleRoot, $ReleaseRoot)) {
    if (-not $Target.StartsWith($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝使用项目目录外的构建路径：$Target"
    }
}

Push-Location $ProjectRoot
try {
    & $PythonExecutable -c "import json; d=json.load(open('docs/feature-status.json',encoding='utf-8')); bad=[f['id'] for f in d['features'] if f['requirement']=='required' and f['status']!='supported']; assert not bad, bad"
    if ($LASTEXITCODE -ne 0) { throw "本系列必需功能尚未完成。" }
    $SourceRevision = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw "无法获取发布源码提交。" }
    $SourceStatus = @(& git status --porcelain)
    if ($LASTEXITCODE -ne 0 -or $SourceStatus.Count -gt 0) { throw "发布构建必须来自干净的已提交工作区。" }
    if (-not $SkipTests) {
        & $PythonExecutable scripts/run_tests.py
        if ($LASTEXITCODE -ne 0) {
            throw "测试未通过，已停止打包。"
        }
    }
    & $PythonExecutable -m PyInstaller --clean --noconfirm `
        --workpath $BuildRoot --distpath $DistRoot deepsonder_pyside6.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败。"
    }

    $BundleExecutable = Join-Path $BundleRoot "DeepSonder-PySide6.exe"
    if (-not (Test-Path -LiteralPath $BundleExecutable -PathType Leaf)) {
        throw "构建完成但未找到 DeepSonder-PySide6.exe。"
    }
    foreach ($Document in @("LICENSE", "PRIVACY.md", "THIRD_PARTY_NOTICES.md")) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot $Document) `
            -Destination (Join-Path $BundleRoot $Document) -Force
    }
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "docs\releases\$Version.md") `
        -Destination (Join-Path $BundleRoot "RELEASE_NOTES.md") -Force
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "licenses") `
        -Destination (Join-Path $BundleRoot "licenses") -Recurse -Force
    $Smoke = Start-Process -FilePath $BundleExecutable `
        -ArgumentList "--self-test" -WindowStyle Hidden -PassThru
    try {
        Wait-Process -Id $Smoke.Id -Timeout 30 -ErrorAction Stop
    }
    catch {
        Stop-Process -Id $Smoke.Id -Force -ErrorAction SilentlyContinue
        throw "打包程序自检超过 30 秒，已终止。"
    }
    $Smoke.Refresh()
    if ($Smoke.ExitCode -ne 0) {
        throw "打包程序自检失败，退出码：$($Smoke.ExitCode)"
    }

    if (Test-Path -LiteralPath $ReleaseRoot) {
        Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null
    $AssetName = "DeepSonder-PySide6-v$Version-windows-x64.zip"
    $AssetPath = Join-Path $ReleaseRoot $AssetName
    Compress-Archive -Path (Join-Path $BundleRoot "*") `
        -DestinationPath $AssetPath -CompressionLevel Optimal -Force
    $Asset = Get-Item -LiteralPath $AssetPath
    $Hash = (Get-FileHash -LiteralPath $AssetPath -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $AssetName" | Set-Content `
        -LiteralPath (Join-Path $ReleaseRoot "SHA256SUMS.txt") -Encoding utf8
    [ordered]@{
        schema_version = 1
        product = "DeepSonder-PySide6"
        version = $Version
        platform = "windows"
        architecture = "x64"
        update_channel = $null
        source_revision = $SourceRevision
        source_dirty = $false
        build_python = (& $PythonExecutable -c "import platform; print(platform.python_version())").Trim()
        build_pyside6 = (& $PythonExecutable -c "import PySide6; print(PySide6.__version__)").Trim()
        build_pyinstaller = (& $PythonExecutable -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
        asset = [ordered]@{
            name = $AssetName
            size = $Asset.Length
            sha256 = $Hash
        }
        published_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    } | ConvertTo-Json | Set-Content `
        -LiteralPath (Join-Path $ReleaseRoot "release-manifest.json") -Encoding utf8

    & $PythonExecutable scripts/verify_release.py $ReleaseRoot
    if ($LASTEXITCODE -ne 0) { throw "发布包审计失败。" }
    Write-Output "DeepSonder-PySide6 v$Version Windows x64 打包完成。"
    Write-Output "发布目录：$ReleaseRoot"
    Write-Output "SHA-256：$Hash"
}
finally {
    Pop-Location
}

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
    throw "Windows 基线包必须使用 Python 3.12，当前为：$PythonVersion"
}

$BuildRoot = Join-Path $ProjectRoot "build\pyinstaller"
$DistRoot = Join-Path $ProjectRoot "dist"
$BundleRoot = Join-Path $DistRoot "Novalist"
$ReleaseRoot = Join-Path $DistRoot "release"
foreach ($Target in @($BuildRoot, $DistRoot, $ReleaseRoot)) {
    if (-not $Target.StartsWith($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝使用项目目录外的构建路径：$Target"
    }
}

Push-Location $ProjectRoot
try {
    if (-not $SkipTests) {
        & $PythonExecutable -m unittest discover -s tests
        if ($LASTEXITCODE -ne 0) {
            throw "测试未通过，已停止打包。"
        }
    }

    & $PythonExecutable -m PyInstaller --clean --noconfirm `
        --workpath $BuildRoot --distpath $DistRoot novalist.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败。"
    }

    $BundleExecutable = Join-Path $BundleRoot "Novalist.exe"
    if (-not (Test-Path -LiteralPath $BundleExecutable -PathType Leaf)) {
        throw "构建完成但未找到 Novalist.exe。"
    }

    foreach ($Document in @("LICENSE", "PRIVACY.md", "THIRD_PARTY_NOTICES.md")) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot $Document) `
            -Destination (Join-Path $BundleRoot $Document) -Force
    }
    $BundleLicenses = Join-Path $BundleRoot "licenses"
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "licenses") `
        -Destination $BundleLicenses -Recurse -Force
    $RequiredLicenses = @(
        "README.md",
        "third-party\APACHE-2.0.txt",
        "third-party\GNU-GPL-3.0.txt",
        "third-party\GNU-LGPL-3.0.txt",
        "third-party\PYINSTALLER-COPYING.txt",
        "third-party\PYTHON-3.12-LICENSE.txt",
        "third-party\SIL-OFL-1.1.txt"
    )
    foreach ($License in $RequiredLicenses) {
        if (-not (Test-Path -LiteralPath (Join-Path $BundleLicenses $License) -PathType Leaf)) {
            throw "打包产物缺少第三方许可证：$License"
        }
    }

    $Smoke = Start-Process -FilePath $BundleExecutable `
        -ArgumentList "--self-test" -WindowStyle Hidden -Wait -PassThru
    if ($Smoke.ExitCode -ne 0) {
        throw "打包程序自检失败，退出码：$($Smoke.ExitCode)"
    }

    if (Test-Path -LiteralPath $ReleaseRoot) {
        Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null

    $AssetName = "Novalist-v$Version-windows-x64.zip"
    $AssetPath = Join-Path $ReleaseRoot $AssetName
    Compress-Archive -Path (Join-Path $BundleRoot "*") `
        -DestinationPath $AssetPath -CompressionLevel Optimal -Force

    $Asset = Get-Item -LiteralPath $AssetPath
    $Hash = (Get-FileHash -LiteralPath $AssetPath -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $AssetName" | Set-Content `
        -LiteralPath (Join-Path $ReleaseRoot "SHA256SUMS.txt") -Encoding utf8

    $Manifest = [ordered]@{
        schema_version = 2
        version = $Version
        channel = if ($Version -match '-(?:beta|b|rc)') { "beta" } else { "stable" }
        platform = "windows"
        architecture = "x64"
        asset = [ordered]@{
            name = $AssetName
            size = $Asset.Length
            sha256 = $Hash
        }
        # The published v2.0.6 package cannot invoke this downloader, while the
        # synced development workspace can use this baseline to test v2.0.7.
        minimum_updater_version = "2.0.6-beta"
        published_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    $Manifest | ConvertTo-Json | Set-Content `
        -LiteralPath (Join-Path $ReleaseRoot "release-manifest.json") -Encoding utf8

    Write-Output "Novalist v$Version Windows x64 打包完成。"
    Write-Output "发布目录：$ReleaseRoot"
    Write-Output "第三方许可证：已复制到压缩包 licenses 目录"
    Write-Output "SHA-256：$Hash"
}
finally {
    Pop-Location
}

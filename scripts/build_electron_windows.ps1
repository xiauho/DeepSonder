param(
    [string]$PythonExecutable = "python",
    [string]$NodeExecutable = "node",
    [switch]$SkipTests,
    [switch]$ExerciseInstaller,
    [switch]$RequireMetadataSignature,
    [string]$ReleasePrivateKeyPath = "",
    [string]$ReleasePublicKeyPath = "",
    [switch]$RequireCodeSigning,
    [string]$CodeSigningCertificatePath = "",
    [string]$CodeSigningCertificatePassword = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ElectronRoot = Join-Path $ProjectRoot "electron"
$BuildRoot = Join-Path $ProjectRoot "build\electron-release"
$SidecarDistRoot = Join-Path $ProjectRoot "dist\electron-sidecar"
$ReleaseRoot = Join-Path $ElectronRoot "release"
$PublishRoot = Join-Path $ReleaseRoot "publish"
$GeneratedRoot = Join-Path $ElectronRoot "generated"

function Assert-WorkspaceChild([string]$Target) {
    $full = [System.IO.Path]::GetFullPath($Target)
    $prefix = $ProjectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the project: $full"
    }
    return $full
}

function Reset-WorkspaceDirectory([string]$Target) {
    $full = Assert-WorkspaceChild $Target
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
    New-Item -ItemType Directory -Path $full -Force | Out-Null
}

function Invoke-Checked([string]$Label, [scriptblock]$Operation) {
    Write-Host "==> $Label"
    & $Operation
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Get-ProjectFingerprint([string]$Root) {
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $entries = Get-ChildItem -LiteralPath $resolved -File -Recurse | Sort-Object FullName | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($resolved.Length).TrimStart("\").Replace("\", "/")
            size = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    return ($entries | ConvertTo-Json -Depth 4 -Compress)
}

function Invoke-PackagedSelfTest([string]$Executable, [string]$ProjectPath, [string]$Label) {
    $before = Get-ProjectFingerprint $ProjectPath
    Write-Host "==> $Label"
    $process = Start-Process -FilePath $Executable -ArgumentList @(
        "--self-test",
        "--self-test-project=$ProjectPath"
    ) -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$Label failed with exit code $($process.ExitCode)"
    }
    $after = Get-ProjectFingerprint $ProjectPath
    if ($before -ne $after) {
        throw "$Label modified the schema-v2 project during its opening check."
    }
}

Set-Location $ProjectRoot
$Version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "VERSION is not a supported semantic version: $Version"
}
$ElectronPackage = Get-Content -LiteralPath (Join-Path $ElectronRoot "package.json") -Raw | ConvertFrom-Json
if ($ElectronPackage.version -ne $Version) {
    throw "VERSION ($Version) and electron/package.json ($($ElectronPackage.version)) differ."
}
$PythonVersion = & $PythonExecutable -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($LASTEXITCODE -ne 0 -or -not $PythonVersion.StartsWith("3.12.")) {
    throw "Python 3.12 is required; found $PythonVersion"
}
$NodeVersion = (& $NodeExecutable --version).TrimStart("v")
if ($LASTEXITCODE -ne 0 -or [int]($NodeVersion.Split(".")[0]) -lt 24) {
    throw "Node.js 24 or newer is required; found $NodeVersion"
}
$SigningRequested = [bool]($RequireMetadataSignature -or $ReleasePrivateKeyPath -or $ReleasePublicKeyPath)
$ReleaseKeyFingerprint = $null
if ($SigningRequested) {
    if (-not (Test-Path -LiteralPath $ReleasePrivateKeyPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ReleasePublicKeyPath -PathType Leaf)) {
        throw "Both Ed25519 release metadata key paths are required."
    }
    $ReleaseKeyFingerprint = (& $NodeExecutable (Join-Path $ElectronRoot "scripts\release-integrity.mjs") fingerprint $ReleasePublicKeyPath).Trim()
    if ($LASTEXITCODE -ne 0 -or $ReleaseKeyFingerprint -notmatch '^[0-9a-f]{64}$') {
        throw "Could not fingerprint the release metadata public key."
    }
}

if (-not $SkipTests) {
    Invoke-Checked "Python regression suite" { & $PythonExecutable -m unittest discover -s tests }
    Push-Location $ElectronRoot
    try {
        Invoke-Checked "Electron regression suite" { & npm test }
    } finally {
        Pop-Location
    }
}

Reset-WorkspaceDirectory $BuildRoot
Reset-WorkspaceDirectory $SidecarDistRoot
Reset-WorkspaceDirectory $ReleaseRoot
Reset-WorkspaceDirectory $GeneratedRoot

$TrustPolicy = if ($SigningRequested) {
    [ordered]@{
        schema_version = 1
        mode = "production"
        key_id = $ReleaseKeyFingerprint
        public_key_pem = (Get-Content -LiteralPath $ReleasePublicKeyPath -Raw)
    }
} else {
    [ordered]@{
        schema_version = 1
        mode = "local-rehearsal"
        key_id = $null
        public_key_pem = $null
    }
}
$TrustPolicy | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $GeneratedRoot "release-trust.json") -Encoding utf8

Invoke-Checked "Build Python Sidecar" {
    & $PythonExecutable -m PyInstaller --clean --noconfirm `
        --workpath (Join-Path $BuildRoot "pyinstaller") `
        --distpath $SidecarDistRoot `
        (Join-Path $ProjectRoot "novalist_sidecar.spec")
}
$SidecarExecutable = Join-Path $SidecarDistRoot "NovalistSidecar.exe"
if (-not (Test-Path -LiteralPath $SidecarExecutable -PathType Leaf)) {
    throw "PyInstaller did not create $SidecarExecutable"
}
if ($RequireCodeSigning) {
    if (-not (Test-Path -LiteralPath $CodeSigningCertificatePath -PathType Leaf)) {
        throw "A PFX code-signing certificate path is required for a signed release."
    }
    $SignTool = Get-Command "signtool.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
    if (-not $SignTool) {
        $WindowsKits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
        if (Test-Path -LiteralPath $WindowsKits) {
            $SignTool = Get-ChildItem -LiteralPath $WindowsKits -Filter "signtool.exe" -File -Recurse |
                Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
                Sort-Object FullName -Descending |
                Select-Object -First 1 -ExpandProperty FullName
        }
    }
    if (-not $SignTool) {
        throw "signtool.exe is required to sign the packaged Sidecar."
    }
    Invoke-Checked "Authenticode-sign Python Sidecar" {
        & $SignTool sign /fd SHA256 /td SHA256 /tr "http://timestamp.digicert.com" `
            /f $CodeSigningCertificatePath /p $CodeSigningCertificatePassword $SidecarExecutable
    }
    if ((Get-AuthenticodeSignature -LiteralPath $SidecarExecutable).Status -ne "Valid") {
        throw "The source Sidecar Authenticode signature is not valid."
    }
}

Push-Location $ElectronRoot
try {
    Invoke-Checked "Build Electron installer and portable recovery ZIP" { & npm run package:win }
} finally {
    Pop-Location
}

$UnpackedExecutable = Join-Path $ReleaseRoot "win-unpacked\Novalist.exe"
$UnpackedSidecar = Join-Path $ReleaseRoot "win-unpacked\resources\sidecar\NovalistSidecar.exe"
foreach ($required in @(
    $UnpackedExecutable,
    $UnpackedSidecar,
    (Join-Path $ReleaseRoot "win-unpacked\resources\legal\LICENSE"),
    (Join-Path $ReleaseRoot "win-unpacked\resources\legal\PRIVACY.md"),
    (Join-Path $ReleaseRoot "win-unpacked\resources\legal\THIRD_PARTY_NOTICES.md"),
    (Join-Path $ReleaseRoot "win-unpacked\resources\legal\licenses\third-party\JAVASCRIPT-MIT-NOTICES.txt"),
    (Join-Path $ReleaseRoot "win-unpacked\LICENSE.electron.txt"),
    (Join-Path $ReleaseRoot "win-unpacked\LICENSES.chromium.html")
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Packaged Electron layout is incomplete: $required"
    }
}
$Installer = @(Get-ChildItem -LiteralPath $ReleaseRoot -File -Filter "Novalist-v$Version-windows-x64-setup.exe")
$PortableZip = @(Get-ChildItem -LiteralPath $ReleaseRoot -File -Filter "Novalist-v$Version-windows-x64.zip")
if ($Installer.Count -ne 1 -or $PortableZip.Count -ne 1) {
    throw "Expected exactly one NSIS installer and one portable ZIP."
}

$RehearsalRoot = Join-Path $BuildRoot "clean-profile"
New-Item -ItemType Directory -Path $RehearsalRoot -Force | Out-Null
$ProjectOutput = @(& $PythonExecutable (Join-Path $ProjectRoot "scripts\create_electron_rehearsal_project.py") $RehearsalRoot)
$ProjectPath = if ($ProjectOutput.Count -gt 0) { [string]$ProjectOutput[-1] } else { "" }
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ProjectPath) -or
    -not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
    throw "Could not create the schema-v2 rehearsal project."
}
Invoke-PackagedSelfTest $UnpackedExecutable $ProjectPath "Clean-profile unpacked Electron/schema-v2 smoke test"

$RecoveryRoot = Join-Path $BuildRoot "portable-recovery"
Reset-WorkspaceDirectory $RecoveryRoot
Expand-Archive -LiteralPath $PortableZip[0].FullName -DestinationPath $RecoveryRoot -Force
$RecoveryExecutable = Join-Path $RecoveryRoot "Novalist.exe"
if (-not (Test-Path -LiteralPath $RecoveryExecutable -PathType Leaf)) {
    throw "The portable recovery ZIP does not contain Novalist.exe at its root."
}
Invoke-PackagedSelfTest $RecoveryExecutable $ProjectPath "Portable recovery/schema-v2 smoke test"

if ($ExerciseInstaller) {
    $InstallRoot = Join-Path $BuildRoot "installed"
    $InstallRoot = Assert-WorkspaceChild $InstallRoot
    try {
        Write-Host "==> Silent per-user installer rehearsal"
        $install = Start-Process -FilePath $Installer[0].FullName -ArgumentList @("/S", "/D=$InstallRoot") -WindowStyle Hidden -Wait -PassThru
        if ($install.ExitCode -ne 0) {
            throw "NSIS installer rehearsal failed with exit code $($install.ExitCode)"
        }
        $InstalledExecutable = Join-Path $InstallRoot "Novalist.exe"
        Invoke-PackagedSelfTest $InstalledExecutable $ProjectPath "Installed clean-profile/schema-v2 smoke test"
    } finally {
        $Uninstaller = Join-Path $InstallRoot "Uninstall Novalist.exe"
        if (Test-Path -LiteralPath $Uninstaller -PathType Leaf) {
            $uninstall = Start-Process -FilePath $Uninstaller -ArgumentList "/S" -WindowStyle Hidden -Wait -PassThru
            if ($uninstall.ExitCode -ne 0) {
                throw "NSIS uninstall rehearsal failed with exit code $($uninstall.ExitCode)"
            }
        }
    }
}

if ($RequireCodeSigning) {
    foreach ($executable in @($UnpackedExecutable, $UnpackedSidecar, $Installer[0].FullName)) {
        $signature = Get-AuthenticodeSignature -LiteralPath $executable
        if ($signature.Status -ne "Valid") {
            throw "Required Authenticode signature is not valid: $executable ($($signature.Status))"
        }
    }
}

New-Item -ItemType Directory -Path $PublishRoot -Force | Out-Null
$PublishedInstaller = Copy-Item -LiteralPath $Installer[0].FullName -Destination $PublishRoot -PassThru
$PublishedPortable = Copy-Item -LiteralPath $PortableZip[0].FullName -Destination $PublishRoot -PassThru

$SignatureMetadata = [ordered]@{ algorithm = "none"; reason = "local-rehearsal" }
if ($SigningRequested) {
    $SignatureMetadata = [ordered]@{
        algorithm = "ed25519"
        key_id = $ReleaseKeyFingerprint
        file = "release-manifest.sig"
    }
}

$Artifacts = @(
    [ordered]@{
        kind = "nsis"
        name = $PublishedInstaller.Name
        size = $PublishedInstaller.Length
        sha256 = (Get-FileHash -LiteralPath $PublishedInstaller.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    },
    [ordered]@{
        kind = "portable_zip"
        name = $PublishedPortable.Name
        size = $PublishedPortable.Length
        sha256 = (Get-FileHash -LiteralPath $PublishedPortable.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
)
$Manifest = [ordered]@{
    schema_version = 3
    package_kind = "electron-only"
    version = $Version
    platform = "windows"
    architecture = "x64"
    entrypoint = "Novalist.exe"
    sidecar = "resources/sidecar/NovalistSidecar.exe"
    artifacts = $Artifacts
    signature = $SignatureMetadata
}
$ManifestPath = Join-Path $PublishRoot "release-manifest.json"
$Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding utf8

$SignaturePath = Join-Path $PublishRoot "release-manifest.sig"
if ($SignatureMetadata.algorithm -eq "ed25519") {
    Invoke-Checked "Sign release metadata with Ed25519" {
        & $NodeExecutable (Join-Path $ElectronRoot "scripts\release-integrity.mjs") sign `
            $ManifestPath $ReleasePrivateKeyPath $ReleasePublicKeyPath $SignaturePath
    }
    Invoke-Checked "Verify signed release metadata" {
        & $NodeExecutable (Join-Path $ElectronRoot "scripts\release-integrity.mjs") verify `
            $PublishRoot $ManifestPath $ReleasePublicKeyPath $SignaturePath
    }
} else {
    Invoke-Checked "Verify local release metadata" {
        & $NodeExecutable (Join-Path $ElectronRoot "scripts\release-integrity.mjs") verify $PublishRoot $ManifestPath
    }
}

$ChecksumFiles = @($PublishedInstaller.FullName, $PublishedPortable.FullName, $ManifestPath)
if (Test-Path -LiteralPath $SignaturePath) { $ChecksumFiles += $SignaturePath }
$ChecksumFiles | ForEach-Object {
    $item = Get-Item -LiteralPath $_
    "{0}  {1}" -f (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $item.Name
} | Set-Content -LiteralPath (Join-Path $PublishRoot "SHA256SUMS.txt") -Encoding ascii

$Report = [ordered]@{
    schema_version = 1
    version = $Version
    package_kind = "electron-only"
    generated_at_utc = [DateTime]::UtcNow.ToString("o")
    python = $PythonVersion
    node = $NodeVersion
    unpacked_smoke_test = "passed"
    portable_recovery_test = "passed"
    installer_test = $(if ($ExerciseInstaller) { "passed" } else { "not_requested" })
    schema_v2_open_was_read_only = $true
    metadata_signature = $SignatureMetadata.algorithm
    embedded_release_key_id = $ReleaseKeyFingerprint
    authenticode_required = [bool]$RequireCodeSigning
}
$Report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PublishRoot "rehearsal-report.json") -Encoding utf8

Write-Host "Electron-only Windows release rehearsal passed: $PublishRoot"

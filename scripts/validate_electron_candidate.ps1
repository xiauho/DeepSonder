param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseDirectory,
    [string]$PublicKeyPath = "",
    [string]$NodeExecutable = "node",
    [string]$ReportPath = "",
    [switch]$AllowUnsignedLocalRehearsal,
    [switch]$RequireCodeSigning,
    [switch]$KeepWorkDirectory
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ReleaseRoot = (Resolve-Path -LiteralPath $ReleaseDirectory).Path
if (-not $ReportPath) {
    $ReportPath = Join-Path $ReleaseRoot "candidate-validation-report.json"
}
$WorkRoot = Join-Path ([IO.Path]::GetTempPath()) ("deepsonder-candidate-" + [guid]::NewGuid().ToString("N"))
$InstallRoot = Join-Path $WorkRoot "installed"
$Uninstaller = Join-Path $InstallRoot "Uninstall DeepSonder-Electron.exe"

function Write-JsonFile([string]$Path, [object]$Value) {
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding utf8
}

function Get-TreeFingerprint([string]$Root) {
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    return (Get-ChildItem -LiteralPath $resolved -File -Recurse | Sort-Object FullName | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($resolved.Length).TrimStart("\").Replace("\", "/")
            size = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    } | ConvertTo-Json -Depth 4 -Compress)
}

function Get-AuthenticodeState([string[]]$Paths) {
    $statuses = @($Paths | ForEach-Object {
        $signature = Get-AuthenticodeSignature -LiteralPath $_
        [ordered]@{ path = $_; status = [string]$signature.Status }
    })
    if (@($statuses | Where-Object status -eq "Valid").Count -eq $statuses.Count) {
        return "passed"
    }
    if (@($statuses | Where-Object status -eq "NotSigned").Count -eq $statuses.Count) {
        return "not_present"
    }
    $summary = ($statuses | ForEach-Object { "$($_.path)=$($_.status)" }) -join "; "
    throw "Authenticode signatures are invalid or inconsistent: $summary"
}

function Invoke-AppSelfTest([string]$Executable, [string]$ProjectPath, [string]$KeyId) {
    $arguments = @("--self-test", "--self-test-project=$ProjectPath")
    if ($KeyId) { $arguments += "--expected-release-key-id=$KeyId" }
    $before = Get-TreeFingerprint $ProjectPath
    $process = Start-Process -FilePath $Executable -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Packaged self-test failed: $Executable ($($process.ExitCode))"
    }
    if ($before -ne (Get-TreeFingerprint $ProjectPath)) {
        throw "Packaged self-test modified the candidate project: $Executable"
    }
}

function New-CandidateProject([string]$Parent) {
    $root = Join-Path $Parent "candidate-project"
    foreach ($directory in @(
        $root,
        (Join-Path $root ".novalist"),
        (Join-Path $root "manuscript"),
        (Join-Path $root "knowledge"),
        (Join-Path $root "proposals"),
        (Join-Path $root "provenance"),
        (Join-Path $root "cache")
    )) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }

    $now = [DateTime]::UtcNow.ToString("o")
    $projectId = [guid]::NewGuid().ToString()
    Write-JsonFile (Join-Path $root ".novalist\project.json") ([ordered]@{
        schema_version = 2
        project_kind = "novalist-manuscript-reconstruction"
        created_by = "phase-19-clean-machine"
        minimum_app_version = "electron-v2-preview"
        legacy_import_policy = "manuscript_only"
    })
    Write-JsonFile (Join-Path $root "project.json") ([ordered]@{
        schema_version = 2
        project_kind = "novalist-manuscript-reconstruction"
        project_id = $projectId
        name = "Phase 19 clean-machine candidate"
        author = "DeepSonder release validation"
        created_at = $now
        updated_at = $now
        content_policy = [ordered]@{
            legacy_import = "manuscript_only"
            knowledge_requires_review = $true
            manuscript_is_primary_source = $true
        }
    })
    $chapterPath = Join-Path $root "manuscript\chapter_0001.md"
    "# 第一章`n`n这是阶段十九的离线正文。`n" | Set-Content -LiteralPath $chapterPath -Encoding utf8 -NoNewline
    $chapterHash = (Get-FileHash -LiteralPath $chapterPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-JsonFile (Join-Path $root "manuscript\index.json") ([ordered]@{
        schema_version = 1
        chapters = @([ordered]@{
            id = "chapter_0001"
            sequence = 1
            title = "第一章"
            path = "manuscript/chapter_0001.md"
            source_name = "phase-19-offline.md"
            source_sha256 = $chapterHash
            content_sha256 = $chapterHash
        })
    })
    Write-JsonFile (Join-Path $root "knowledge\entities.json") ([ordered]@{ schema_version = 1; entities = @() })
    Write-JsonFile (Join-Path $root "knowledge\relations.json") ([ordered]@{ schema_version = 1; relations = @() })
    Write-JsonFile (Join-Path $root "knowledge\world_rules.json") ([ordered]@{ schema_version = 1; rules = @(); hidden_rules = @() })
    Write-JsonFile (Join-Path $root "knowledge\timeline.json") ([ordered]@{ schema_version = 1; events = @(); hidden_events = @(); diagnostics = @() })
    Write-JsonFile (Join-Path $root "knowledge\curation.json") ([ordered]@{
        schema_version = 1
        entity_overrides = @{}
        entity_merges = @{}
        relation_overrides = @{}
        character_field_overrides = @{}
        world_overrides = @{}
        event_overrides = @{}
        event_order = @()
        operations = @()
    })
    Write-JsonFile (Join-Path $root "proposals\index.json") ([ordered]@{ schema_version = 1; batches = @() })
    Write-JsonFile (Join-Path $root "provenance\imports.json") ([ordered]@{ schema_version = 1; imports = @() })
    Write-JsonFile (Join-Path $root "provenance\evidence.json") ([ordered]@{ schema_version = 1; evidence = @() })
    Write-JsonFile (Join-Path $root "provenance\curation_log.json") ([ordered]@{ schema_version = 1; operations = @() })
    return $root
}

$Succeeded = $false
try {
    New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null
    $ManifestPath = Join-Path $ReleaseRoot "release-manifest.json"
    $SignaturePath = Join-Path $ReleaseRoot "release-manifest.sig"
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "Release manifest is missing."
    }
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $ExpectedReleaseTier = if ([string]$Manifest.version -like "*-*") { "prerelease" } else { "stable" }
    if ($Manifest.release_tier -notin @("prerelease", "stable") -or
        $Manifest.release_tier -ne $ExpectedReleaseTier) {
        throw "Release tier does not match the candidate version."
    }
    $TierAuthenticodePolicy = if ($Manifest.release_tier -eq "stable") { "required" } else { "optional" }
    $CodeSigningRequired = [bool](-not $AllowUnsignedLocalRehearsal -and ($RequireCodeSigning -or $TierAuthenticodePolicy -eq "required"))
    $KeyId = ""
    if ($AllowUnsignedLocalRehearsal) {
        & $NodeExecutable (Join-Path $ProjectRoot "scripts\release-integrity.mjs") verify $ReleaseRoot $ManifestPath
        if ($LASTEXITCODE -ne 0) { throw "Release metadata verification failed." }
    } else {
        if (-not (Test-Path -LiteralPath $PublicKeyPath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $SignaturePath -PathType Leaf)) {
            throw "Signed candidate validation requires the trusted public key and detached signature."
        }
        & $NodeExecutable (Join-Path $ProjectRoot "scripts\release-integrity.mjs") verify `
            $ReleaseRoot $ManifestPath $PublicKeyPath $SignaturePath
        if ($LASTEXITCODE -ne 0) { throw "Release metadata signature verification failed." }
        $KeyId = (& $NodeExecutable (Join-Path $ProjectRoot "scripts\release-integrity.mjs") fingerprint $PublicKeyPath).Trim()
        if ($LASTEXITCODE -ne 0) { throw "Release public-key fingerprinting failed." }
        if ($Manifest.signature.key_id -ne $KeyId) {
            throw "Manifest key ID differs from the independently supplied public key."
        }
    }
    $InstallerName = ($Manifest.artifacts | Where-Object kind -eq "nsis").name
    $PortableName = ($Manifest.artifacts | Where-Object kind -eq "portable_zip").name
    $InstallerPath = Join-Path $ReleaseRoot $InstallerName
    $PortablePath = Join-Path $ReleaseRoot $PortableName

    $RecoveryRoot = Join-Path $WorkRoot "portable-recovery"
    Expand-Archive -LiteralPath $PortablePath -DestinationPath $RecoveryRoot -Force
    $RecoveryExecutable = Join-Path $RecoveryRoot "DeepSonder-Electron.exe"
    $RecoverySidecar = Join-Path $RecoveryRoot "resources\sidecar\DeepSonderElectronSidecar.exe"
    $AuthenticodeState = if ($AllowUnsignedLocalRehearsal) {
        "not_required"
    } else {
        Get-AuthenticodeState @($InstallerPath, $RecoveryExecutable, $RecoverySidecar)
    }
    if ($CodeSigningRequired -and $AuthenticodeState -ne "passed") {
        throw "Authenticode code signing is required for this release tier."
    }

    $ProjectPath = New-CandidateProject $WorkRoot
    $OriginalFingerprint = Get-TreeFingerprint $ProjectPath
    $BackupPath = Join-Path $WorkRoot "candidate-project-backup.zip"
    Compress-Archive -LiteralPath $ProjectPath -DestinationPath $BackupPath

    try {
        $install = Start-Process -FilePath $InstallerPath -ArgumentList @("/S", "/D=$InstallRoot") -WindowStyle Hidden -Wait -PassThru
        if ($install.ExitCode -ne 0) { throw "NSIS install failed with exit code $($install.ExitCode)." }
        $InstalledExecutable = Join-Path $InstallRoot "DeepSonder-Electron.exe"
        if (-not $AllowUnsignedLocalRehearsal) {
            $InstalledAuthenticode = Get-AuthenticodeState @(
                $InstalledExecutable,
                (Join-Path $InstallRoot "resources\sidecar\DeepSonderElectronSidecar.exe")
            )
            if ($InstalledAuthenticode -ne $AuthenticodeState) {
                throw "Installed Authenticode state differs from the packaged candidate."
            }
        }
        Invoke-AppSelfTest $InstalledExecutable $ProjectPath $KeyId
    } finally {
        if (Test-Path -LiteralPath $Uninstaller -PathType Leaf) {
            $uninstall = Start-Process -FilePath $Uninstaller -ArgumentList "/S" -WindowStyle Hidden -Wait -PassThru
            if ($uninstall.ExitCode -ne 0) { throw "NSIS uninstall failed with exit code $($uninstall.ExitCode)." }
        }
    }

    Invoke-AppSelfTest $RecoveryExecutable $ProjectPath $KeyId
    $RestoredRoot = Join-Path $WorkRoot "restored"
    Expand-Archive -LiteralPath $BackupPath -DestinationPath $RestoredRoot -Force
    $RestoredProject = Join-Path $RestoredRoot "candidate-project"
    if ($OriginalFingerprint -ne (Get-TreeFingerprint $RestoredProject)) {
        throw "Restored project differs from its verified backup."
    }
    Invoke-AppSelfTest $RecoveryExecutable $RestoredProject $KeyId

    $Os = Get-CimInstance Win32_OperatingSystem
    $Report = [ordered]@{
        schema_version = 2
        result = "passed"
        package_kind = "electron-only"
        release_tier = $Manifest.release_tier
        version = $Manifest.version
        source_commit = $Manifest.source_commit
        release_key_id = $(if ($KeyId) { $KeyId } else { $null })
        unsigned_local_rehearsal = [bool]$AllowUnsignedLocalRehearsal
        os_caption = $Os.Caption
        os_version = $Os.Version
        os_build = $Os.BuildNumber
        generated_at_utc = [DateTime]::UtcNow.ToString("o")
        manifest_signature = $(if ($AllowUnsignedLocalRehearsal) { "not_required" } else { "passed" })
        authenticode = $AuthenticodeState
        authenticode_policy = $TierAuthenticodePolicy
        nsis_install_and_uninstall = "passed"
        schema_v2_open_was_read_only = $true
        schema_v2_workflow_surface = "passed"
        schema_v2_ai_review_surface = "passed"
        portable_recovery = "passed"
        project_backup_restore = "passed"
        installer_sha256 = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        portable_sha256 = (Get-FileHash -LiteralPath $PortablePath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $ReportParent = Split-Path -Parent ([IO.Path]::GetFullPath($ReportPath))
    New-Item -ItemType Directory -Path $ReportParent -Force | Out-Null
    Write-JsonFile $ReportPath $Report
    $Succeeded = $true
    Write-Host "Electron release candidate validation passed: $ReportPath"
} finally {
    if (-not $KeepWorkDirectory -and (Test-Path -LiteralPath $WorkRoot)) {
        $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\deepsonder-candidate-"
        $resolvedWork = [IO.Path]::GetFullPath($WorkRoot)
        if (-not $resolvedWork.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean an unexpected validation path: $resolvedWork"
        }
        Remove-Item -LiteralPath $resolvedWork -Recurse -Force
    }
}

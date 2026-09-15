param(
    [string]$TargetRoot = "D:\GitHub-store\DeepSonder",
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedTarget = [IO.Path]::GetFullPath($TargetRoot)

if (-not (Test-Path -LiteralPath $resolvedTarget -PathType Container)) {
    throw "Electron target directory does not exist: $resolvedTarget"
}
$targetItems = @(Get-ChildItem -LiteralPath $resolvedTarget -Force)
if ($targetItems.Count -ne 0 -and -not $Resume) {
    throw "Electron target directory must be empty: $resolvedTarget"
}
if ($Resume -and (Test-Path -LiteralPath (Join-Path $resolvedTarget ".git"))) {
    throw "Refusing to resume over an initialized Git repository: $resolvedTarget"
}

function Copy-WorkspaceFile([string]$SourceRelative, [string]$TargetRelative) {
    $source = [IO.Path]::GetFullPath((Join-Path $SourceRoot $SourceRelative))
    $target = [IO.Path]::GetFullPath((Join-Path $resolvedTarget $TargetRelative))
    $targetPrefix = $resolvedTarget.TrimEnd("\") + "\"
    if (-not $target.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to write outside the Electron target: $target"
    }
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required split source is missing: $source"
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $target
}

# Flatten the tracked Electron workspace into the new repository root.
$electronFiles = @(& git -C $SourceRoot -c core.quotepath=false ls-files electron)
foreach ($relative in $electronFiles) {
    if ($relative -eq "electron/README.md") { continue }
    $withoutPrefix = $relative.Substring("electron/".Length)
    if ($withoutPrefix.StartsWith("tests/")) {
        Copy-WorkspaceFile $relative ("electron-tests/" + $withoutPrefix.Substring("tests/".Length))
    } else {
        Copy-WorkspaceFile $relative $withoutPrefix
    }
}

# Copy the self-contained Python backend. Generated packages remain excluded.
foreach ($root in @("core", "application", "sidecar")) {
    foreach ($relative in @(& git -C $SourceRoot -c core.quotepath=false ls-files $root)) {
        Copy-WorkspaceFile $relative $relative
    }
}

foreach ($relative in @(
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "LICENSE",
    "PRIVACY.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "VERSION",
    "config.example.json",
    "sidecar_main.py",
    "docs/feature-parity.json",
    "assets/app_icon.ico",
    "assets/app_icon.png"
)) {
    Copy-WorkspaceFile $relative $relative
}

foreach ($relative in @(& git -C $SourceRoot -c core.quotepath=false ls-files licenses)) {
    Copy-WorkspaceFile $relative $relative
}

Copy-WorkspaceFile "novalist_sidecar.spec" "deepsonder_electron_sidecar.spec"
Copy-WorkspaceFile "requirements-build.txt" "requirements-build.txt"
Copy-WorkspaceFile "scripts/build_electron_windows.ps1" "scripts/build_windows.ps1"
Copy-WorkspaceFile "scripts/validate_electron_candidate.ps1" "scripts/validate_electron_candidate.ps1"
foreach ($relative in @(
    "scripts/create_electron_rehearsal_project.py",
    "scripts/evaluate_reconstruction.py",
    "scripts/verify_dsh_prompt_transport.py",
    "scripts/verify_v2_ai_workflows.py"
)) {
    Copy-WorkspaceFile $relative $relative
}

# Copy backend tests without pulling PySide6 UI or legacy updater coverage into
# the Electron repository. Both shared synthetic fixture sets remain available.
Copy-WorkspaceFile "tests/__init__.py" "tests/__init__.py"
foreach ($relative in @(& git -C $SourceRoot -c core.quotepath=false ls-files tests/fixtures)) {
    Copy-WorkspaceFile $relative $relative
}
foreach ($relative in @(& git -C $SourceRoot -c core.quotepath=false ls-files projects/demo_novel)) {
    Copy-WorkspaceFile $relative $relative
}

$excludedTests = @(
    "test_electron_source_launcher.py",
    "test_packaging_runtime.py",
    "test_update_controller.py",
    "test_update_dialog.py",
    "test_update_download_controller.py",
    "test_update_download_service.py",
    "test_update_install_service.py",
    "test_update_installer.py",
    "test_update_service.py"
)
foreach ($relative in @(& git -C $SourceRoot -c core.quotepath=false ls-files "tests/test_*.py")) {
    if ((Split-Path -Leaf $relative) -in $excludedTests) { continue }
    $content = Get-Content -LiteralPath (Join-Path $SourceRoot $relative) -Raw
    if ($content -match "PySide6" -or $content -match "(?m)^\s*(?:from|import)\s+ui(?:\.|\s|$)") {
        continue
    }
    Copy-WorkspaceFile $relative $relative
}

Write-Host "Seeded standalone Electron workspace: $resolvedTarget"

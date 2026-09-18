# DeepSonder privacy notice

This notice covers the **DeepSonder-PySide6** desktop series, displayed as **DeepSonder**. DeepSonder-PySide6 is a local desktop application. It does not operate a DeepSonder cloud account, analytics service, telemetry endpoint, or automatic update service.

## Local data

Writing projects, story memory, recognized characters, canon, reports and local configuration remain on the computer unless the user deliberately copies or exports them. The application profile is isolated below the operating system's `DeepSonder/PySide6` configuration and cache directories.

The legacy-project importer reads an old Novalist source project without modifying it. It creates a separate project from the project name, author and ordered chapter Markdown only. It does not import old memory, recognized characters, canon, relationship graphs, AI results, caches, proposals, update state or trash.

## AI tasks

DeepSonder-PySide6 invokes the external `dsh` command only after the user starts an AI action or connection test. The complete business prompt is written to a randomly named, access-restricted temporary task file and removed on success, failure, timeout or cancellation when the operating system permits.

The content sent through `dsh` depends on the selected task and may include manuscript text, outlines, story memory and relevant canon. The destination service, credentials, retention and provider privacy policy are controlled by the user's DeepSeek Harness configuration. DeepSonder-PySide6 does not request or store an API key.

Diagnostic context reports shown by the application contain local paths, chapter identifiers, hashes, sizes and inclusion decisions. Treat them as private local data and review them before sharing.

## Packaging and updates

The PySide6 package contains no Electron runtime, Node.js dependency, Sidecar service or old automatic updater. Release manifests are build-audit records for this series only and are not consumed by DeepSonder-Electron.

Never commit `.env`, API keys, credentials, private projects, generated output, local profiles or task files to source control.

Current release instructions are in [PACKAGING.md](PACKAGING.md). [Historical notices and release notes](docs/archive/README.md) describe earlier versions and do not define the current data flow.

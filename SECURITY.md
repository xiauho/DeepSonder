# DeepSonder-PySide6 security policy

## Reporting a vulnerability

Please do not place API keys, private manuscripts, personal information, or
working exploit details in a public issue. If the repository has GitHub private
vulnerability reporting enabled, use that channel. Otherwise, open a minimal
issue asking the maintainer for a private contact method.

Include the affected version, reproduction conditions, expected impact and any
safe mitigation you have identified.

## Supported version

This policy covers the current DeepSonder-PySide6 series. Report the series,
[VERSION](VERSION) value and source revision when available. Historical
Novalist tags and archived release notes do not indicate current support or an
automatic upgrade path.

Security fixes target the current series maintained in this repository. Users
should also keep Python, PySide6 and DeepSeek Harness updated after checking
compatibility with their environment.

Use [current packaging instructions](PACKAGING.md) for builds and installation.
Preserve legacy import boundaries and user-data protections when addressing a
vulnerability. The [archive](docs/archive/README.md) is for historical reference.

# Bundled third-party license texts

The Windows package places this directory next to `Novalist.exe` so the
license terms for redistributed runtimes and fonts remain directly available
after extraction.

| Component | Version used by the baseline | License text |
| --- | --- | --- |
| Qt for Python / PySide6 / Shiboken | 6.8.3 | `third-party/GNU-LGPL-3.0.txt` and `third-party/GNU-GPL-3.0.txt` |
| CPython runtime | 3.12.x | `third-party/PYTHON-3.12-LICENSE.txt` |
| PyInstaller bootloader | 6.22.2 | `third-party/PYINSTALLER-COPYING.txt` |
| React / React DOM, Cytoscape.js, CodeMirror / Lezer | versions locked by `electron/package-lock.json` | `third-party/JAVASCRIPT-MIT-NOTICES.txt` |
| Material Symbols | bundled font files | `third-party/APACHE-2.0.txt` |
| Hanken Grotesk, Inter, JetBrains Mono and Source Serif 4 | bundled font files | `third-party/SIL-OFL-1.1.txt` |

See `THIRD_PARTY_NOTICES.md` for component notices and upstream links.
Electron additionally places `LICENSE.electron.txt` and
`LICENSES.chromium.html` at the package root.

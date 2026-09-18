# Third-party notices

DeepSonder-PySide6 uses the following third-party components. The application
code remains separately copyrightable and is not made subject to the GPL solely
by using the LGPL option offered by Qt for Python.

## Qt for Python / PySide6 6.8.3

- Copyright © The Qt Company Ltd. and other contributors.
- License used by this project: GNU Lesser General Public License v3.0 only
  (`LGPL-3.0-only`). PySide6 also offers GPL and commercial licensing options.
- Source and license information: <https://code.qt.io/cgit/pyside/pyside-setup.git/>
- Official licensing documentation: <https://doc.qt.io/qtforpython-6/>

Source executions load PySide6 and Qt from the user's Python environment.
Packaged Windows distributions include the corresponding Python, PySide6, Qt,
and Shiboken runtime files. Nothing in this project restricts rights granted by
the LGPL, including lawful reverse engineering for debugging modifications to
the LGPL-covered libraries. Release packaging must retain the applicable
license texts and notices alongside the application.

The packaged license texts are available under `licenses/third-party/`:
`GNU-LGPL-3.0.txt` and `GNU-GPL-3.0.txt`.

## DeepSeek Harness (optional external tool)

- Copyright © 2026 DeepSeek.
- License: MIT License.
- Official project: <https://github.com/deepseek-ai/deepseek-harness>

DeepSeek Harness is not bundled with or redistributed by this repository.
DeepSonder-PySide6 can invoke a separately installed `dsh` command when the user enables
AI-assisted features.

## Historical frontend notices

The current PySide6 package does not include the retired Electron frontend.
The existing `licenses/third-party/JAVASCRIPT-MIT-NOTICES.txt` is retained as a
historical attribution record; it does not describe a current JavaScript runtime
dependency. Original third-party license texts and copyright notices remain
unchanged. Earlier frontend release details are in the [archive](docs/archive/README.md).

## Bundled Stitch typography assets

The native interface bundles the local font files under `assets/fonts/` for
visual consistency with the Stitch reference screens. The font families are
Hanken Grotesk, Inter, JetBrains Mono, Source Serif 4, and Material Symbols
Outlined. Their upstream license terms remain applicable; the first four are
distributed under the SIL Open Font License 1.1 and Material Symbols under the
Apache License 2.0.

The packaged texts are `licenses/third-party/SIL-OFL-1.1.txt` and
`licenses/third-party/APACHE-2.0.txt`.

- Google Fonts: <https://fonts.google.com/> and <https://developers.google.com/fonts/faq>
- Material Symbols: <https://github.com/google/material-design-icons>

## Python

- Copyright © Python Software Foundation.
- Python Software Foundation License Version 2.
- <https://docs.python.org/3/license.html>

This notice is informational and does not replace the license texts shipped by
the corresponding upstream projects.

The packaged CPython 3.12 license is
`licenses/third-party/PYTHON-3.12-LICENSE.txt`.

## PyInstaller bootloader

- Copyright © 2005-2023 the PyInstaller development team and contributors.
- License: GNU GPL version 2 or later, with the PyInstaller bootloader
  exception permitting distribution as part of a combined executable.
- Official project: <https://pyinstaller.org/>

The complete terms and bootloader exception are packaged as
`licenses/third-party/PYINSTALLER-COPYING.txt`.

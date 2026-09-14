# Phase 23A: renderer bundle budget

## Objective

Keep the Markdown editor lazy-loaded while reducing its initial feature payload,
removing the renderer chunk-size warning, and turning the expected split into a
repeatable build gate. This phase changes renderer composition only; it does not
change the Sidecar protocol, project schema, document persistence, or packaged
release policy.

## Baseline

Before this phase, Vite emitted one `MarkdownEditor` chunk of 605,510 bytes and
reported that it exceeded the default 500 kB warning threshold. The editor used
the `codemirror` convenience package and its broad `basicSetup` extension set,
including functionality that Novalist did not expose.

## Implementation

- Replace `basicSetup` with the explicit extensions the product uses: line
  numbers, history, selection drawing, Markdown syntax highlighting, active-line
  highlighting, selection-match highlighting, search, and the required keymaps.
- Depend directly on the selected `@codemirror/*` packages and remove the
  `codemirror` meta package.
- Define stable Rolldown groups for the editor runtime and Markdown language
  support while retaining the existing React-level lazy feature boundary.
- Run `npm run check:bundles` after every renderer build. The gate requires the
  editor groups, limits every runtime JavaScript chunk to 480 KiB, and limits
  the lazy `MarkdownEditor` shell to 32 KiB.

## Result

The verified production renderer build contains these JavaScript chunks:

| Chunk | Bytes |
| --- | ---: |
| `GraphView` | 445,204 |
| `editor-core` | 353,692 |
| `index` | 295,875 |
| `markdown-language` | 182,870 |
| `MarkdownEditor` shell | 1,579 |

The largest runtime chunk is 160,306 bytes smaller than the baseline, a 26.47%
reduction. The complete lazy editor payload is 538,141 bytes, 67,369 bytes or
11.13% below the former single chunk. No renderer chunk-size warning remains.

The explicit extension set preserves the currently supported editor behavior:
Markdown highlighting, optional line numbers, undo/redo, search/replace,
selection and active-line rendering, wrapping, change notification, and the
existing revision-safe autosave path.

## Verification

- clean `npm ci`: 327 packages installed, zero reported vulnerabilities;
- `npm test`: 16 of 16 Electron tests passed;
- `npm run build`: type checking, renderer build, and bundle budget passed;
- `npm run capture`: preview self-test and visual capture passed;
- the Electron self-test waited for `.codemirror-editor .cm-editor`, confirming
  that the split editor runtime loaded and rendered before testing later views.

## Release boundary and follow-up

The source-bound Phase 22C installer and portable ZIP were built from commit
`f20808020d6637bb6152318161811c5a879e0925`; they do not contain this uncommitted
Phase 23A optimization. A new candidate must be built after these changes are
committed before it can represent the optimized renderer.

Phase 23B should exercise large manuscripts and dense relationship graphs,
record responsiveness and memory baselines, and add targeted virtualization or
graph-loading limits only where measurements show a problem. `GraphView` is now
the largest runtime chunk and remains below the enforced budget.

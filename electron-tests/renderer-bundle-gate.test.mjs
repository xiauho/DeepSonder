import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_RENDERER_CHUNK_BYTES,
  validateRendererBundles,
} from "../scripts/check-renderer-bundles.mjs";

function bundleFiles() {
  return [
    { name: "index-example.js", size: 300_000 },
    { name: "GraphView-example.js", size: 445_000 },
    { name: "MarkdownEditor-example.js", size: 2_000 },
    { name: "editor-core-example.js", size: 354_000 },
    { name: "markdown-language-example.js", size: 183_000 },
    { name: "index-example.js.map", size: 2_000_000 },
  ];
}

test("renderer bundle gate accepts split lazy editor chunks", () => {
  const result = validateRendererBundles(bundleFiles());
  assert.equal(result.chunkCount, 5);
  assert.equal(result.largestChunk.name, "GraphView-example.js");
  assert.equal(result.editorShell.name, "MarkdownEditor-example.js");
});

test("renderer bundle gate rejects a monolithic or incomplete editor", () => {
  const oversized = bundleFiles();
  oversized[2].size = MAX_RENDERER_CHUNK_BYTES + 1;
  assert.throws(() => validateRendererBundles(oversized), /exceed/u);

  const missingLanguage = bundleFiles().filter((item) => !item.name.startsWith("markdown-language-"));
  assert.throws(() => validateRendererBundles(missingLanguage), /markdown-language-/u);
});

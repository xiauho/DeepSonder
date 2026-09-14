import { readdir, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const MAX_RENDERER_CHUNK_BYTES = 480 * 1024;
export const MAX_EDITOR_SHELL_BYTES = 32 * 1024;

export function validateRendererBundles(files) {
  const JavaScript = files.filter((item) => item.name.endsWith(".js"));
  if (JavaScript.length === 0) {
    throw new Error("Renderer bundle directory does not contain JavaScript chunks.");
  }
  const oversized = JavaScript.filter((item) => item.size > MAX_RENDERER_CHUNK_BYTES);
  if (oversized.length > 0) {
    throw new Error(`Renderer chunks exceed ${MAX_RENDERER_CHUNK_BYTES} bytes: ${oversized.map((item) => `${item.name} (${item.size})`).join(", ")}`);
  }
  const editorShells = JavaScript.filter((item) => /^MarkdownEditor-.*\.js$/u.test(item.name));
  if (editorShells.length !== 1 || editorShells[0].size > MAX_EDITOR_SHELL_BYTES) {
    throw new Error("Markdown editor shell is missing, duplicated, or no longer lightweight.");
  }
  for (const prefix of ["editor-core-", "markdown-language-"]) {
    if (!JavaScript.some((item) => item.name.startsWith(prefix))) {
      throw new Error(`Expected lazy editor chunk is missing: ${prefix}`);
    }
  }
  return {
    chunkCount: JavaScript.length,
    largestChunk: JavaScript.reduce((largest, item) => item.size > largest.size ? item : largest),
    editorShell: editorShells[0],
  };
}

async function main(argv) {
  const assetDirectory = path.resolve(argv[0] ?? "dist/renderer/assets");
  const names = await readdir(assetDirectory);
  const files = await Promise.all(names.map(async (name) => ({
    name,
    size: (await stat(path.join(assetDirectory, name))).size,
  })));
  const result = validateRendererBundles(files);
  process.stdout.write(
    `renderer-bundle-gate: ${result.chunkCount} chunks, largest ${result.largestChunk.name} (${result.largestChunk.size} bytes), editor shell ${result.editorShell.size} bytes\n`,
  );
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}

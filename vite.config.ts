import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist/renderer",
    emptyOutDir: false,
    sourcemap: false,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "editor-core",
              test: /node_modules[\\/](?:@codemirror[\\/](?:autocomplete|commands|language|search|state|view)|@lezer[\\/](?:common|highlight|lr))[\\/]/,
              priority: 20,
            },
            {
              name: "markdown-language",
              test: /node_modules[\\/](?:@codemirror|@lezer)[\\/]/,
              priority: 10,
            },
          ],
        },
      },
    },
  },
});

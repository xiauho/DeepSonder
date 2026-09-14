import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { fileURLToPath } from "node:url";

import cytoscape from "cytoscape";

import { SidecarClient } from "../dist/electron/main/sidecar-client.js";
import { chooseGraphLayoutName } from "../dist/electron/shared/graph-layout-policy.js";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const electronRoot = path.resolve(scriptDirectory, "..");
const repositoryRoot = path.resolve(electronRoot, "..");
const projectPython = path.join(repositoryRoot, ".venv", "Scripts", "python.exe");
const python = process.env.NOVALIST_PYTHON || (existsSync(projectPython) ? projectPython : "python");
const reportArgument = process.argv.find((argument) => argument.startsWith("--report="));
const reportPath = path.resolve(
  reportArgument?.slice("--report=".length) || path.join(repositoryRoot, "build", "phase-23b-performance.json"),
);

const BUDGETS = {
  sidecarStartMs: 5_000,
  projectCreateMs: 5_000,
  manuscriptSaveMs: 5_000,
  manuscriptOpenMs: 3_000,
  graphConstructMs: 1_000,
  graphLayoutMs: 1_500,
  graphFilterMs: 1_000,
  graphHeapDeltaBytes: 256 * 1024 * 1024,
};

function elapsed(startedAt) {
  return Number((performance.now() - startedAt).toFixed(2));
}

async function measure(action) {
  const startedAt = performance.now();
  const value = await action();
  return { value, durationMs: elapsed(startedAt) };
}

function createLargeManuscript(targetCharacters = 1_000_000) {
  const paragraph = "林砚沿着雾港潮湿的石阶前行，记录潮声、灯塔与人物关系的细微变化。".repeat(6);
  const sections = [];
  let length = 0;
  for (let index = 1; length < targetCharacters; index += 1) {
    const section = `## 场景 ${index}\n\n${paragraph}\n\n`;
    sections.push(section);
    length += section.length;
  }
  return sections.join("").slice(0, targetCharacters);
}

function createGraphElements(nodeCount, edgeCount) {
  const elements = [];
  for (let index = 0; index < nodeCount; index += 1) {
    elements.push({ data: { id: `node-${index}`, label: `人物 ${index}` } });
  }
  for (let index = 0; index < edgeCount; index += 1) {
    const layer = Math.floor(index / nodeCount);
    elements.push({
      data: {
        id: `edge-${index}`,
        source: `node-${index % nodeCount}`,
        target: `node-${(index * 37 + 11 + layer * 13) % nodeCount}`,
        label: `关系 ${index % 12}`,
      },
    });
  }
  return elements;
}

async function benchmarkManuscript(temporaryRoot) {
  const client = new SidecarClient({
    command: python,
    args: ["-m", "sidecar"],
    cwd: repositoryRoot,
    requestTimeoutMs: 15_000,
  });
  try {
    const started = await measure(() => client.start());
    const sourcePath = path.join(repositoryRoot, "tests", "fixtures", "electron_migration", "golden_project");
    const scanned = await client.request("manuscript.scanImport", { sourcePath });
    const created = await measure(() => client.request("project.createV2", {
      parentDirectory: temporaryRoot,
      name: "phase-23b-large-manuscript",
      author: "Novalist performance harness",
      planDigest: scanned.plan.digest,
    }));
    const initial = await client.request("manuscript.open", { chapterId: "chapter_0001" });
    const content = createLargeManuscript();
    const heapBefore = process.memoryUsage().heapUsed;
    const saved = await measure(() => client.request("manuscript.save", {
      chapterId: "chapter_0001",
      content,
      expectedRevision: initial.document.revision,
    }));
    const opened = await measure(() => client.request("manuscript.open", { chapterId: "chapter_0001" }));
    const normalizedContent = content.endsWith("\n") ? content : `${content}\n`;
    assert.equal(opened.value.document.content, normalizedContent);
    return {
      requestedCharacters: content.length,
      persistedCharacters: opened.value.document.content.length,
      sidecarStartMs: started.durationMs,
      projectCreateMs: created.durationMs,
      manuscriptSaveMs: saved.durationMs,
      manuscriptOpenMs: opened.durationMs,
      clientHeapDeltaBytes: Math.max(0, process.memoryUsage().heapUsed - heapBefore),
    };
  } finally {
    await client.stop();
  }
}

function benchmarkGraph() {
  const nodeCount = 500;
  const edgeCount = 2_000;
  const elements = createGraphElements(nodeCount, edgeCount);
  const layoutName = chooseGraphLayoutName(nodeCount);
  const heapBefore = process.memoryUsage().heapUsed;
  const constructStarted = performance.now();
  const graph = cytoscape({ headless: true, elements });
  const constructMs = elapsed(constructStarted);
  const layoutStarted = performance.now();
  graph.layout({ name: layoutName, animate: false, fit: false, padding: 48 }).run();
  const layoutMs = elapsed(layoutStarted);
  const filterStarted = performance.now();
  graph.batch(() => {
    graph.nodes().forEach((node, index) => node.toggleClass("filtered-out", index % 3 === 0));
    graph.edges().forEach((edge, index) => edge.toggleClass("filtered-out", index % 4 === 0));
  });
  const filterMs = elapsed(filterStarted);
  const heapDeltaBytes = Math.max(0, process.memoryUsage().heapUsed - heapBefore);
  graph.destroy();
  return { nodeCount, edgeCount, layoutName, constructMs, layoutMs, filterMs, heapDeltaBytes };
}

function enforceBudgets(manuscript, graph) {
  const checks = {
    sidecarStart: manuscript.sidecarStartMs <= BUDGETS.sidecarStartMs,
    projectCreate: manuscript.projectCreateMs <= BUDGETS.projectCreateMs,
    manuscriptSave: manuscript.manuscriptSaveMs <= BUDGETS.manuscriptSaveMs,
    manuscriptOpen: manuscript.manuscriptOpenMs <= BUDGETS.manuscriptOpenMs,
    graphConstruct: graph.constructMs <= BUDGETS.graphConstructMs,
    graphLayout: graph.layoutMs <= BUDGETS.graphLayoutMs,
    graphFilter: graph.filterMs <= BUDGETS.graphFilterMs,
    graphHeap: graph.heapDeltaBytes <= BUDGETS.graphHeapDeltaBytes,
  };
  const failures = Object.entries(checks).filter(([, passed]) => !passed).map(([name]) => name);
  if (failures.length > 0) throw new Error(`Large-workspace performance budgets failed: ${failures.join(", ")}`);
  return checks;
}

async function main() {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "novalist-phase-23b-"));
  try {
    const manuscript = await benchmarkManuscript(temporaryRoot);
    const graph = benchmarkGraph();
    const checks = enforceBudgets(manuscript, graph);
    const sourceCommit = execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: repositoryRoot,
      encoding: "utf8",
    }).trim();
    const workingTreeDirty = execFileSync("git", ["status", "--porcelain"], {
      cwd: repositoryRoot,
      encoding: "utf8",
    }).trim().length > 0;
    const report = {
      schemaVersion: 1,
      result: "passed",
      generatedAtUtc: new Date().toISOString(),
      sourceCommit,
      workingTreeDirty,
      runtime: { node: process.version, platform: process.platform, architecture: process.arch },
      budgets: BUDGETS,
      manuscript,
      graph,
      checks,
    };
    await mkdir(path.dirname(reportPath), { recursive: true });
    await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
    process.stdout.write(`large-workspace-benchmark: passed (${reportPath})\n`);
    process.stdout.write(`${JSON.stringify({ manuscript, graph })}\n`);
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
});

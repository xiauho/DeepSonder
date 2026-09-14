import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SHA256 = /^[0-9a-f]{64}$/u;
const VERSION = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/u;
const SOURCE_COMMIT = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u;
const AI_CASES = new Map([
  ["connection", null],
  ["check", "discarded"],
  ["expand", "discarded"],
  ["continuation", "writing_applied_to_disposable_project"],
  ["memory", "memory_committed_to_disposable_project"],
]);

function assertCandidateGate(report) {
  if (report?.schema_version !== 1 || report.result !== "passed" ||
      report.package_kind !== "electron-only" || !VERSION.test(report.version) ||
      !SOURCE_COMMIT.test(report.source_commit) || !SHA256.test(report.release_key_id) ||
      !SHA256.test(report.installer_sha256) || !SHA256.test(report.portable_sha256)) {
    throw new Error("候选包报告缺少正式发布所需的版本、源码、签名或制品绑定。");
  }
  if (!Array.isArray(report.clients) || report.clients.length !== 2 ||
      !report.clients.some((item) => item.client === "windows-10") ||
      !report.clients.some((item) => item.client === "windows-11")) {
    throw new Error("候选包报告没有同时覆盖 Windows 10 和 Windows 11。");
  }
  const requiredGates = [
    "signatures",
    "install_and_uninstall",
    "schema_v2_read_only_open",
    "schema_v2_workflow_surface",
    "schema_v2_ai_review_surface",
    "backup_and_portable_recovery",
  ];
  for (const gate of requiredGates) {
    if (report.gates?.[gate] !== "passed") {
      throw new Error(`候选包报告缺少通过门禁：${gate}。`);
    }
  }
}

function assertAiGate(report) {
  if (report?.schema_version !== 1 || report.passed !== true ||
      report.data_scope !== "disposable_synthetic_manuscript_only" ||
      !VERSION.test(report.app_version) || !SOURCE_COMMIT.test(report.source_commit)) {
    throw new Error("真实 DSH 报告无效、未通过或未绑定源码提交。");
  }
  if (!Array.isArray(report.cases) || report.cases.length !== AI_CASES.size) {
    throw new Error("真实 DSH 报告的验收路径集合不完整。");
  }
  const seen = new Set();
  for (const item of report.cases) {
    if (!AI_CASES.has(item?.kind) || seen.has(item.kind) || item.passed !== true ||
        item.status !== "succeeded" || item.error_type !== "") {
      throw new Error("真实 DSH 报告包含失败、重复或未知的验收路径。");
    }
    seen.add(item.kind);
    const adoption = AI_CASES.get(item.kind);
    if (adoption !== null && (item.review_first !== true || item.adoption !== adoption)) {
      throw new Error(`真实 DSH ${item.kind} 路径未证明审核优先边界。`);
    }
    if (item.kind === "memory" && item.memory_file_created !== true) {
      throw new Error("真实 DSH memory 路径未证明显式采纳结果。");
    }
  }
}

export function buildReleaseReadiness(candidateGate, aiGate) {
  assertCandidateGate(candidateGate);
  assertAiGate(aiGate);
  if (candidateGate.version !== aiGate.app_version) {
    throw new Error("真实 DSH 报告与候选包版本不一致。");
  }
  if (candidateGate.source_commit !== aiGate.source_commit) {
    throw new Error("真实 DSH 报告与候选包不是由同一源码提交生成。");
  }
  return {
    schema_version: 1,
    result: "passed",
    decision: "eligible_for_manual_release_review",
    package_kind: "electron-only",
    version: candidateGate.version,
    source_commit: candidateGate.source_commit,
    release_key_id: candidateGate.release_key_id,
    installer_sha256: candidateGate.installer_sha256,
    portable_sha256: candidateGate.portable_sha256,
    evidence: {
      signed_windows_clients: ["windows-10", "windows-11"],
      schema_v2_live_ai_cases: [...AI_CASES.keys()],
      synthetic_remote_data_only: true,
      automatic_publication_authorized: false,
    },
  };
}

async function main(argv) {
  if (argv.length !== 3) {
    throw new Error("用法：release-readiness-gate.mjs <candidate-gate.json> <ai-gate.json> <output.json>");
  }
  const [candidateGate, aiGate] = await Promise.all(argv.slice(0, 2).map(async (file) =>
    JSON.parse(await readFile(file, "utf8"))));
  const readiness = buildReleaseReadiness(candidateGate, aiGate);
  await writeFile(argv[2], `${JSON.stringify(readiness, null, 2)}\n`, "utf8");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}

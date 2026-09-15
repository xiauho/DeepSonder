import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SHA256 = /^[0-9a-f]{64}$/u;
const VERSION = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/u;
const SOURCE_COMMIT = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u;

function assertReport(report, expectedClient) {
  if (report?.schema_version !== 2 || report.result !== "passed" ||
      report.package_kind !== "electron-only" || report.unsigned_local_rehearsal !== false) {
    throw new Error(`${expectedClient} 验收报告不是通过的正式 Electron 候选。`);
  }
  if (!VERSION.test(report.version) || !SOURCE_COMMIT.test(report.source_commit) ||
      !SHA256.test(report.release_key_id) ||
      !SHA256.test(report.installer_sha256) || !SHA256.test(report.portable_sha256)) {
    throw new Error(`${expectedClient} 验收报告的版本、密钥或产物摘要无效。`);
  }
  const expectedTier = report.version.includes("-") ? "prerelease" : "stable";
  if (report.release_tier !== expectedTier) {
    throw new Error(`${expectedClient} 验收报告的发布级别与版本不一致。`);
  }
  for (const [key, expected] of Object.entries({
    manifest_signature: "passed",
    nsis_install_and_uninstall: "passed",
    schema_v2_workflow_surface: "passed",
    schema_v2_ai_review_surface: "passed",
    portable_recovery: "passed",
    project_backup_restore: "passed",
  })) {
    if (report[key] !== expected) {
      throw new Error(`${expectedClient} 验收报告缺少通过门禁：${key}。`);
    }
  }
  if (report.authenticode !== "passed" &&
      !(report.release_tier === "prerelease" && report.authenticode === "not_present")) {
    throw new Error(`${expectedClient} 验收报告的 Authenticode 状态不符合发布级别。`);
  }
  const expectedAuthenticodePolicy = report.release_tier === "stable" ? "required" : "optional";
  if (report.authenticode_policy !== expectedAuthenticodePolicy) {
    throw new Error(`${expectedClient} 验收报告的 Authenticode 策略无效。`);
  }
  if (report.schema_v2_open_was_read_only !== true) {
    throw new Error(`${expectedClient} 验收未证明 schema-v2 打开过程只读。`);
  }
  const caption = String(report.os_caption ?? "").toLowerCase();
  if (!caption.includes(expectedClient.replace("-", " "))) {
    throw new Error(`${expectedClient} 报告来自错误的客户端系统。`);
  }
}

export function compareCandidateReports(windows10, windows11) {
  assertReport(windows10, "windows-10");
  assertReport(windows11, "windows-11");
  for (const key of ["release_tier", "version", "source_commit", "release_key_id", "authenticode", "installer_sha256", "portable_sha256"]) {
    if (windows10[key] !== windows11[key]) {
      throw new Error(`Windows 10/11 验收报告不属于同一候选：${key}。`);
    }
  }
  return {
    schema_version: 2,
    result: "passed",
    package_kind: "electron-only",
    release_tier: windows10.release_tier,
    version: windows10.version,
    source_commit: windows10.source_commit,
    release_key_id: windows10.release_key_id,
    authenticode: windows10.authenticode,
    installer_sha256: windows10.installer_sha256,
    portable_sha256: windows10.portable_sha256,
    clients: [
      { client: "windows-10", os_version: windows10.os_version, os_build: windows10.os_build },
      { client: "windows-11", os_version: windows11.os_version, os_build: windows11.os_build },
    ],
    gates: {
      metadata_signature: "passed",
      authenticode: windows10.authenticode,
      install_and_uninstall: "passed",
      schema_v2_read_only_open: "passed",
      schema_v2_workflow_surface: "passed",
      schema_v2_ai_review_surface: "passed",
      backup_and_portable_recovery: "passed",
    },
  };
}

async function main(argv) {
  if (argv.length !== 3) {
    throw new Error("用法：candidate-report-gate.mjs <windows10.json> <windows11.json> <output.json>");
  }
  const reports = await Promise.all(argv.slice(0, 2).map(async (file) =>
    JSON.parse(await readFile(file, "utf8"))));
  const gate = compareCandidateReports(reports[0], reports[1]);
  await writeFile(argv[2], `${JSON.stringify(gate, null, 2)}\n`, "utf8");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}

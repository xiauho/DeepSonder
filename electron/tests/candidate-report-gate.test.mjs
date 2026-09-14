import assert from "node:assert/strict";
import test from "node:test";

import { compareCandidateReports } from "../scripts/candidate-report-gate.mjs";

const digest = "a".repeat(64);
const keyId = "b".repeat(64);

function report(client) {
  return {
    schema_version: 1,
    result: "passed",
    package_kind: "electron-only",
    version: "2.1.0-beta",
    release_key_id: keyId,
    unsigned_local_rehearsal: false,
    os_caption: `Microsoft ${client.replace("-", " ")} Pro`,
    os_version: "10.0",
    os_build: client === "windows-10" ? "19045" : "26100",
    manifest_signature: "passed",
    authenticode: "passed",
    nsis_install_and_uninstall: "passed",
    schema_v2_open_was_read_only: true,
    schema_v2_workflow_surface: "passed",
    schema_v2_ai_review_surface: "passed",
    portable_recovery: "passed",
    project_backup_restore: "passed",
    installer_sha256: digest,
    portable_sha256: "c".repeat(64),
  };
}

test("candidate report gate binds Windows 10 and 11 to one signed build", () => {
  const gate = compareCandidateReports(report("windows-10"), report("windows-11"));
  assert.equal(gate.result, "passed");
  assert.equal(gate.release_key_id, keyId);
  assert.equal(gate.clients.length, 2);
  assert.equal(gate.gates.schema_v2_ai_review_surface, "passed");
});

test("candidate report gate rejects mismatched artifacts and missing v2 AI proof", () => {
  const windows10 = report("windows-10");
  const windows11 = report("windows-11");
  windows11.portable_sha256 = "d".repeat(64);
  assert.throws(
    () => compareCandidateReports(windows10, windows11),
    /不属于同一候选/u,
  );
  windows11.portable_sha256 = windows10.portable_sha256;
  windows11.schema_v2_ai_review_surface = "not_checked";
  assert.throws(
    () => compareCandidateReports(windows10, windows11),
    /schema_v2_ai_review_surface/u,
  );
});

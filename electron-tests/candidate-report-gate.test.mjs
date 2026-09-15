import assert from "node:assert/strict";
import test from "node:test";

import { compareCandidateReports } from "../scripts/candidate-report-gate.mjs";

const digest = "a".repeat(64);
const keyId = "b".repeat(64);
const sourceCommit = "1".repeat(40);

function report(client, { version = "2.1.0-beta", authenticode = "not_present" } = {}) {
  const releaseTier = version.includes("-") ? "prerelease" : "stable";
  return {
    schema_version: 2,
    result: "passed",
    package_kind: "electron-only",
    release_tier: releaseTier,
    version,
    source_commit: sourceCommit,
    release_key_id: keyId,
    unsigned_local_rehearsal: false,
    os_caption: `Microsoft ${client.replace("-", " ")} Pro`,
    os_version: "10.0",
    os_build: client === "windows-10" ? "19045" : "26100",
    manifest_signature: "passed",
    authenticode,
    authenticode_policy: releaseTier === "stable" ? "required" : "optional",
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

test("candidate report gate accepts an Ed25519-signed unsigned prerelease", () => {
  const gate = compareCandidateReports(report("windows-10"), report("windows-11"));
  assert.equal(gate.result, "passed");
  assert.equal(gate.release_key_id, keyId);
  assert.equal(gate.source_commit, sourceCommit);
  assert.equal(gate.release_tier, "prerelease");
  assert.equal(gate.authenticode, "not_present");
  assert.equal(gate.gates.metadata_signature, "passed");
  assert.equal(gate.clients.length, 2);
  assert.equal(gate.gates.schema_v2_ai_review_surface, "passed");
});

test("candidate report gate accepts a signed stable release and rejects unsigned stable output", () => {
  const signed = compareCandidateReports(
    report("windows-10", { version: "2.1.0", authenticode: "passed" }),
    report("windows-11", { version: "2.1.0", authenticode: "passed" }),
  );
  assert.equal(signed.release_tier, "stable");
  assert.equal(signed.authenticode, "passed");

  assert.throws(
    () => compareCandidateReports(
      report("windows-10", { version: "2.1.0" }),
      report("windows-11", { version: "2.1.0" }),
    ),
    /Authenticode/u,
  );

  const invalid = report("windows-10", { authenticode: "invalid" });
  assert.throws(
    () => compareCandidateReports(invalid, report("windows-11")),
    /Authenticode/u,
  );
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
  windows11.schema_v2_ai_review_surface = "passed";
  windows11.source_commit = "2".repeat(40);
  assert.throws(
    () => compareCandidateReports(windows10, windows11),
    /不属于同一候选/u,
  );
  windows11.source_commit = windows10.source_commit;
  windows11.authenticode = "passed";
  assert.throws(
    () => compareCandidateReports(windows10, windows11),
    /不属于同一候选/u,
  );
});

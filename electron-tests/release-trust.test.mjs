import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  loadReleaseTrustPolicy,
  parseReleaseTrustPolicy,
  publicKeyId,
} from "../dist/electron/main/release-trust.js";

test("production trust policy pins the exact Ed25519 public key", () => {
  const { publicKey } = generateKeyPairSync("ed25519");
  const publicKeyPem = publicKey.export({ type: "spki", format: "pem" });
  const policy = parseReleaseTrustPolicy({
    schema_version: 1,
    mode: "production",
    key_id: publicKeyId(publicKeyPem),
    public_key_pem: publicKeyPem,
  });
  assert.equal(policy.mode, "production");
  assert.equal(policy.keyId, publicKeyId(publicKeyPem));

  assert.throws(() => parseReleaseTrustPolicy({
    schema_version: 1,
    mode: "production",
    key_id: "0".repeat(64),
    public_key_pem: publicKeyPem,
  }), /固定指纹/u);
});

test("local rehearsal trust policy cannot smuggle a key", () => {
  assert.deepEqual(parseReleaseTrustPolicy({
    schema_version: 1,
    mode: "local-rehearsal",
    key_id: null,
    public_key_pem: null,
  }), {
    schemaVersion: 1,
    mode: "local-rehearsal",
    keyId: null,
    publicKeyPem: null,
  });
  assert.throws(() => parseReleaseTrustPolicy({
    schema_version: 1,
    mode: "local-rehearsal",
    key_id: "0".repeat(64),
    public_key_pem: "untrusted",
  }), /不得声明/u);
});
test("packaged trust loader accepts PowerShell UTF-8 BOM output", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "deepsonder-trust-bom-"));
  const generated = path.join(root, "generated");
  await mkdir(generated);
  await writeFile(path.join(generated, "release-trust.json"), "\uFEFF" + JSON.stringify({
    schema_version: 1,
    mode: "local-rehearsal",
    key_id: null,
    public_key_pem: null,
  }));
  try {
    assert.equal(loadReleaseTrustPolicy(root).mode, "local-rehearsal");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

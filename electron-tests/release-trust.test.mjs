import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import test from "node:test";

import {
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

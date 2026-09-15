import { createHash, createPublicKey } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

const SHA256 = /^[0-9a-f]{64}$/u;

export interface ReleaseTrustPolicy {
  schemaVersion: 1;
  mode: "local-rehearsal" | "production";
  keyId: string | null;
  publicKeyPem: string | null;
}

export function publicKeyId(publicKeyPem: string): string {
  const key = createPublicKey(publicKeyPem);
  const der = key.export({ type: "spki", format: "der" });
  return createHash("sha256").update(der).digest("hex");
}

export function parseReleaseTrustPolicy(value: unknown): ReleaseTrustPolicy {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("发布信任策略不是对象。");
  }
  const input = value as Record<string, unknown>;
  if (input.schema_version !== 1 ||
      (input.mode !== "local-rehearsal" && input.mode !== "production")) {
    throw new Error("发布信任策略版本或模式无效。");
  }
  if (input.mode === "local-rehearsal") {
    if (input.key_id !== null || input.public_key_pem !== null) {
      throw new Error("本地演练策略不得声明发布密钥。");
    }
    return { schemaVersion: 1, mode: input.mode, keyId: null, publicKeyPem: null };
  }
  if (typeof input.key_id !== "string" || !SHA256.test(input.key_id) ||
      typeof input.public_key_pem !== "string" || input.public_key_pem.trim().length === 0) {
    throw new Error("生产发布信任策略缺少有效公钥或指纹。");
  }
  if (publicKeyId(input.public_key_pem) !== input.key_id) {
    throw new Error("发布公钥与固定指纹不一致。");
  }
  return {
    schemaVersion: 1,
    mode: input.mode,
    keyId: input.key_id,
    publicKeyPem: input.public_key_pem,
  };
}

export function loadReleaseTrustPolicy(applicationRoot: string): ReleaseTrustPolicy {
  const policyPath = path.join(applicationRoot, "generated", "release-trust.json");
  try {
    return parseReleaseTrustPolicy(JSON.parse(readFileSync(policyPath, "utf8")));
  } catch (error) {
    throw new Error(
      `无法加载内嵌发布信任策略：${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

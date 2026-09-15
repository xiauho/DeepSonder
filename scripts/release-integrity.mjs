import { createHash, createPrivateKey, createPublicKey, sign, verify } from "node:crypto";
import { readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SHA256 = /^[0-9a-f]{64}$/u;
const VERSION = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/u;
const SOURCE_COMMIT = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u;

export async function sha256File(filePath) {
  return createHash("sha256").update(await readFile(filePath)).digest("hex");
}

export function publicKeyFingerprint(publicKeyPem) {
  const key = createPublicKey(publicKeyPem);
  const der = key.export({ type: "spki", format: "der" });
  return createHash("sha256").update(der).digest("hex");
}

export async function validateReleaseManifest(releaseDirectory, manifestPath) {
  const bytes = await readFile(manifestPath);
  const value = JSON.parse(bytes.toString("utf8"));
  if (value?.schema_version !== 4 || value.package_kind !== "electron-only") {
    throw new Error("发布清单不是 Electron-only schema 4。 ");
  }
  if (!VERSION.test(value.version) || value.platform !== "windows" || value.architecture !== "x64") {
    throw new Error("发布清单版本或平台无效。");
  }
  const expectedTier = value.version.includes("-") ? "prerelease" : "stable";
  if (value.release_tier !== expectedTier) {
    throw new Error("发布清单级别与版本不一致。");
  }
  if (value.entrypoint !== "DeepSonder-Electron.exe" || value.sidecar !== "resources/sidecar/DeepSonderElectronSidecar.exe") {
    throw new Error("发布清单入口或 Sidecar 路径无效。");
  }
  if (!Array.isArray(value.artifacts) || value.artifacts.length !== 2) {
    throw new Error("发布清单必须包含安装包和便携恢复包。");
  }
  const kinds = new Set();
  for (const artifact of value.artifacts) {
    if (!artifact || !["nsis", "portable_zip"].includes(artifact.kind) || kinds.has(artifact.kind)) {
      throw new Error("发布产物类型无效或重复。");
    }
    kinds.add(artifact.kind);
    if (typeof artifact.name !== "string" || path.basename(artifact.name) !== artifact.name) {
      throw new Error("发布产物名称必须是安全的文件名。");
    }
    const artifactPath = path.join(releaseDirectory, artifact.name);
    const details = await stat(artifactPath);
    if (!details.isFile() || details.size !== artifact.size || !SHA256.test(artifact.sha256)) {
      throw new Error(`发布产物元数据无效：${artifact.name}`);
    }
    if (await sha256File(artifactPath) !== artifact.sha256) {
      throw new Error(`发布产物摘要不匹配：${artifact.name}`);
    }
  }
  if (!kinds.has("nsis") || !kinds.has("portable_zip")) {
    throw new Error("发布产物集合不完整。");
  }
  const signature = value.signature;
  if (!signature || !["none", "ed25519"].includes(signature.algorithm)) {
    throw new Error("发布清单签名声明无效。");
  }
  if (signature.algorithm === "ed25519" &&
      (!SHA256.test(signature.key_id) || signature.file !== "release-manifest.sig" ||
       !SOURCE_COMMIT.test(value.source_commit))) {
    throw new Error("Ed25519 签名元数据无效。");
  }
  if (signature.algorithm === "none" && signature.reason !== "local-rehearsal") {
    throw new Error("未签名产物只能标记为本地演练。");
  }
  return { value, bytes };
}

export async function signReleaseManifest(manifestPath, privateKeyPath, publicKeyPath, signaturePath) {
  const bytes = await readFile(manifestPath);
  const privateKey = createPrivateKey(await readFile(privateKeyPath));
  const publicKeyPem = await readFile(publicKeyPath);
  const publicKey = createPublicKey(publicKeyPem);
  const manifest = JSON.parse(bytes.toString("utf8"));
  const fingerprint = publicKeyFingerprint(publicKeyPem);
  if (manifest?.signature?.algorithm !== "ed25519" || manifest.signature.key_id !== fingerprint) {
    throw new Error("发布清单中的签名密钥指纹与受信公钥不一致。");
  }
  const signature = sign(null, bytes, privateKey);
  if (!verify(null, bytes, publicKey, signature)) {
    throw new Error("生成的发布签名无法通过受信公钥验证。");
  }
  await writeFile(signaturePath, signature.toString("base64") + "\n", { encoding: "ascii" });
}

export async function verifyReleaseSignature(manifestPath, publicKeyPath, signaturePath) {
  const bytes = await readFile(manifestPath);
  const publicKeyPem = await readFile(publicKeyPath);
  const manifest = JSON.parse(bytes.toString("utf8"));
  if (manifest?.signature?.algorithm !== "ed25519" ||
      manifest.signature.key_id !== publicKeyFingerprint(publicKeyPem)) {
    throw new Error("发布清单不信任所提供的公钥。");
  }
  const signature = Buffer.from((await readFile(signaturePath, "ascii")).trim(), "base64");
  if (!verify(null, bytes, createPublicKey(publicKeyPem), signature)) {
    throw new Error("发布清单签名验证失败。");
  }
}

async function main(argv) {
  const [mode, ...args] = argv;
  if (mode === "verify" && (args.length === 2 || args.length === 4)) {
    await validateReleaseManifest(args[0], args[1]);
    if (args.length === 4) await verifyReleaseSignature(args[1], args[2], args[3]);
    return;
  }
  if (mode === "sign" && args.length === 4) {
    await signReleaseManifest(args[0], args[1], args[2], args[3]);
    return;
  }
  if (mode === "fingerprint" && args.length === 1) {
    process.stdout.write(`${publicKeyFingerprint(await readFile(args[0]))}\n`);
    return;
  }
  throw new Error("用法：release-integrity.mjs verify <releaseDir> <manifest> [publicKey signature] | sign <manifest> <privateKey> <publicKey> <signature> | fingerprint <publicKey>");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}

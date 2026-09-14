import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  publicKeyFingerprint,
  sha256File,
  signReleaseManifest,
  validateReleaseManifest,
  verifyReleaseSignature,
} from "../scripts/release-integrity.mjs";

async function fixture(signature) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "novalist-release-integrity-"));
  const installer = "Novalist-v2.1.0-beta-windows-x64-setup.exe";
  const portable = "Novalist-v2.1.0-beta-windows-x64.zip";
  await writeFile(path.join(directory, installer), "installer fixture");
  await writeFile(path.join(directory, portable), "portable fixture");
  const artifacts = await Promise.all([
    ["nsis", installer],
    ["portable_zip", portable],
  ].map(async ([kind, name]) => ({
    kind,
    name,
    size: (await readFile(path.join(directory, name))).byteLength,
    sha256: await sha256File(path.join(directory, name)),
  })));
  const manifestPath = path.join(directory, "release-manifest.json");
  await writeFile(manifestPath, JSON.stringify({
    schema_version: 4,
    package_kind: "electron-only",
    release_tier: "prerelease",
    version: "2.1.0-beta",
    platform: "windows",
    architecture: "x64",
    source_commit: "1".repeat(40),
    entrypoint: "Novalist.exe",
    sidecar: "resources/sidecar/NovalistSidecar.exe",
    artifacts,
    signature,
  }, null, 2) + "\n");
  return { directory, manifestPath, installer };
}

test("unsigned local rehearsal validates artifacts and rejects tampering", async () => {
  const sample = await fixture({ algorithm: "none", reason: "local-rehearsal" });
  try {
    await validateReleaseManifest(sample.directory, sample.manifestPath);
    await writeFile(path.join(sample.directory, sample.installer), "tampered");
    await assert.rejects(
      validateReleaseManifest(sample.directory, sample.manifestPath),
      /元数据无效|摘要不匹配/u,
    );
  } finally {
    await rm(sample.directory, { recursive: true, force: true });
  }
});

test("Ed25519 metadata signature binds the exact release manifest", async () => {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  const publicPem = publicKey.export({ type: "spki", format: "pem" });
  const sample = await fixture({
    algorithm: "ed25519",
    key_id: publicKeyFingerprint(publicPem),
    file: "release-manifest.sig",
  });
  const privatePath = path.join(sample.directory, "release-private.pem");
  const publicPath = path.join(sample.directory, "release-public.pem");
  const signaturePath = path.join(sample.directory, "release-manifest.sig");
  try {
    await writeFile(privatePath, privateKey.export({ type: "pkcs8", format: "pem" }));
    await writeFile(publicPath, publicPem);
    await signReleaseManifest(sample.manifestPath, privatePath, publicPath, signaturePath);
    await verifyReleaseSignature(sample.manifestPath, publicPath, signaturePath);

    await writeFile(sample.manifestPath, `${await readFile(sample.manifestPath, "utf8")} `);
    await assert.rejects(
      verifyReleaseSignature(sample.manifestPath, publicPath, signaturePath),
      /签名验证失败/u,
    );
  } finally {
    await rm(sample.directory, { recursive: true, force: true });
  }
});

test("signed metadata rejects a missing source commit", async () => {
  const { publicKey } = generateKeyPairSync("ed25519");
  const publicPem = publicKey.export({ type: "spki", format: "pem" });
  const sample = await fixture({
    algorithm: "ed25519",
    key_id: publicKeyFingerprint(publicPem),
    file: "release-manifest.sig",
  });
  try {
    const manifest = JSON.parse(await readFile(sample.manifestPath, "utf8"));
    delete manifest.source_commit;
    await writeFile(sample.manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
    await assert.rejects(
      validateReleaseManifest(sample.directory, sample.manifestPath),
      /签名元数据无效/u,
    );
  } finally {
    await rm(sample.directory, { recursive: true, force: true });
  }
});

test("release tier must match the semantic version", async () => {
  const sample = await fixture({ algorithm: "none", reason: "local-rehearsal" });
  try {
    const manifest = JSON.parse(await readFile(sample.manifestPath, "utf8"));
    manifest.release_tier = "stable";
    await writeFile(sample.manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
    await assert.rejects(
      validateReleaseManifest(sample.directory, sample.manifestPath),
      /级别与版本不一致/u,
    );
  } finally {
    await rm(sample.directory, { recursive: true, force: true });
  }
});

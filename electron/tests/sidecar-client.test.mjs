import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { cp, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { SidecarClient } from "../dist/electron/main/sidecar-client.js";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const electronRoot = path.resolve(testDirectory, "..");
const repositoryRoot = path.resolve(electronRoot, "..");
const projectPython = path.join(repositoryRoot, ".venv", "Scripts", "python.exe");
const python = process.env.NOVALIST_PYTHON || (existsSync(projectPython) ? projectPython : "python");

test("Electron main client handshakes and reads the golden project", async () => {
  const client = new SidecarClient({
    command: python,
    args: ["-m", "sidecar"],
    cwd: repositoryRoot,
    requestTimeoutMs: 5_000,
  });
  try {
    const handshake = await client.start();
    assert.equal(handshake.protocolVersion, 1);
    assert.ok(handshake.supportedMethods.includes("document.save"));
    assert.ok(handshake.supportedMethods.includes("ai.start"));
    assert.ok(handshake.supportedMethods.includes("graph.snapshot"));
    assert.ok(handshake.supportedMethods.includes("knowledge.updateCharacterField"));
    assert.ok(handshake.supportedMethods.includes("knowledge.openCard"));
    assert.ok(handshake.supportedMethods.includes("knowledge.saveAuthorCard"));

    const fixture = path.join(
      repositoryRoot,
      "tests",
      "fixtures",
      "electron_migration",
      "golden_project",
    );
    const opened = await client.request("project.open", { path: fixture });
    assert.equal(opened.opened.migration.fromSchema, 1);
    assert.equal(opened.opened.migration.changedFiles.length, 0);

    const result = await client.request("document.open", {
      category: "章节",
      path: "outline/chapters/chapter_02.md",
    });
    assert.equal(result.document.relativePath, "outline/chapters/chapter_02.md");
    assert.match(result.document.revision, /^v1:/);

    const graph = await client.request("graph.snapshot");
    assert.equal(graph.graph.nodes.length, 3);
    assert.equal(graph.graph.edges.length, 3);
    assert.ok(graph.graph.edges.every((edge) => edge.directed === true));
    assert.equal(graph.graph.warnings[0].code, "missing_character_card");
  } finally {
    await client.stop();
  }
});

test("Electron main client surfaces structured Sidecar errors", async () => {
  const client = new SidecarClient({
    command: python,
    args: ["-m", "sidecar"],
    cwd: repositoryRoot,
    requestTimeoutMs: 5_000,
  });
  try {
    await client.start();
    const status = await client.request("ai.status");
    assert.equal(status.ai.active, null);
    assert.ok(status.ai.supportedKinds.includes("expand"));
    await assert.rejects(
      client.request("filesystem.read", { path: "forbidden" }),
      (error) => error?.name === "SidecarRpcError" && error.code === "METHOD_NOT_FOUND",
    );
  } finally {
    await client.stop();
  }
});

test("Electron main client manages project content and typed trash", async () => {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "novalist-electron-phase4-"));
  const fixture = path.join(repositoryRoot, "tests", "fixtures", "electron_migration", "golden_project");
  const project = path.join(temporaryRoot, "project");
  await cp(fixture, project, { recursive: true });
  const client = new SidecarClient({
    command: python,
    args: ["-m", "sidecar"],
    cwd: repositoryRoot,
    requestTimeoutMs: 5_000,
  });
  try {
    await client.start();
    await client.request("project.open", { path: project });
    const initial = await client.request("project.snapshot");
    assert.equal(initial.snapshot.chapters.length, 2);
    assert.equal(initial.snapshot.nextChapterId, "chapter_03");

    const created = await client.request("document.createChapter", {
      title: "第三章",
      chapterId: "chapter_03",
    });
    assert.equal(created.snapshot.chapters.length, 3);

    const removed = await client.request("document.delete", {
      kind: "chapter",
      itemId: "chapter_03",
      path: "outline/chapters/chapter_03.md",
    });
    assert.equal(removed.snapshot.chapters.length, 2);
    const trashed = await client.request("trash.list");
    assert.equal(trashed.trash.items.length, 1);

    const restored = await client.request("trash.restore", {
      kind: "chapter",
      trashId: trashed.trash.items[0].trashId,
      conflictPolicy: "error",
    });
    assert.equal(restored.snapshot.chapters.length, 3);
    assert.equal(restored.trash.items.length, 0);
  } finally {
    await client.stop();
    await rm(temporaryRoot, { recursive: true, force: true });
  }
});

test("Electron main client imports and edits a schema-v2 manuscript", async () => {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "novalist-electron-v2-"));
  const manuscriptPath = path.join(temporaryRoot, "source.md");
  await writeFile(manuscriptPath, "# 第一章\n\n林砚拆开信封。苏乔低声说：先别点灯。\n\n[[关系:林砚|调查搭档|苏乔]] [[角色字段:林砚|身份|雾港调查员]] [[世界:雾港|地点|终年被浓雾笼罩的港城]] [[事件:雨夜|收到密信|两人在雾港收到匿名密信|林砚、苏乔|雾港]] [[事件:雨夜|前往灯塔|林砚决定前往灯塔|林砚|雾港]]\n", "utf8");
  const client = new SidecarClient({
    command: python,
    args: ["-m", "sidecar"],
    cwd: repositoryRoot,
    requestTimeoutMs: 5_000,
  });
  try {
    const handshake = await client.start();
    assert.equal(handshake.projectSchema.maximum, 2);
    assert.ok(handshake.supportedMethods.includes("manuscript.scanImport"));

    const scanned = await client.request("manuscript.scanImport", { sourcePath: manuscriptPath });
    assert.equal(scanned.plan.chapters.length, 1);
    assert.equal(scanned.plan.sourceKind, "external_manuscript");

    const created = await client.request("project.createV2", {
      parentDirectory: temporaryRoot,
      name: "雾港来信",
      author: "测试作者",
      planDigest: scanned.plan.digest,
    });
    assert.equal(created.opened.schemaVersion, 2);

    const snapshot = await client.request("manuscript.snapshot");
    assert.equal(snapshot.snapshot.itemCount, 1);
    const started = await client.request("reconstruction.start");
    assert.ok(["queued", "running"].includes(started.task.status));
    assert.equal(started.task.requestedMode, "local");
    let reconstructionTask;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const status = await client.request("reconstruction.taskStatus");
      reconstructionTask = status.reconstructionTask.recent;
      if (["succeeded", "failed", "cancelled"].includes(reconstructionTask?.status)) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.equal(reconstructionTask.status, "succeeded");
    const generated = await client.request("reconstruction.batch", { batchId: reconstructionTask.batchId });
    assert.equal(generated.batch.extraction.producer, "local");
    assert.equal(generated.batch.extraction.fallbackUsed, false);
    const entityProposals = generated.batch.proposals.filter((item) => item.kind === "entity");
    const relationProposal = generated.batch.proposals.find((item) => item.kind === "relation");
    const worldProposal = generated.batch.proposals.find((item) => item.kind === "world");
    const fieldProposal = generated.batch.proposals.find((item) => item.kind === "character_field");
    const eventProposals = generated.batch.proposals.filter((item) => item.kind === "event");
    assert.equal(entityProposals.length, 2);
    assert.equal(eventProposals.length, 2);
    assert.ok(relationProposal && worldProposal && fieldProposal);
    const bulkReviewed = await client.request("reconstruction.reviewMany", {
      batchId: generated.batch.batchId,
      decisions: [
        ...entityProposals.map((proposal) => ({ proposalId: proposal.proposalId, decision: "accepted" })),
        { proposalId: relationProposal.proposalId, decision: "accepted" },
        { proposalId: worldProposal.proposalId, decision: "accepted" },
        { proposalId: fieldProposal.proposalId, decision: "accepted" },
        ...eventProposals.map((proposal) => ({ proposalId: proposal.proposalId, decision: "accepted" })),
      ],
    });
    assert.equal(bulkReviewed.batch.status, "reviewed");
    assert.ok(bulkReviewed.batch.proposals.every((proposal) => proposal.reviewMode === "batch"));
    assert.ok(bulkReviewed.batch.proposals.every((proposal) => proposal.reviewedAt.length > 0));
    const graph = await client.request("graph.snapshot");
    assert.equal(graph.graph.nodes.length, 5);
    assert.equal(graph.graph.edges.length, 6);
    assert.ok(graph.graph.edges.every((edge) => edge.sourceKind === "reviewed_v2"));
    assert.equal(graph.graph.nodes.find((item) => item.nodeKind === "event").order, 1);
    const knowledge = await client.request("knowledge.snapshot");
    assert.equal(knowledge.knowledge.entities.length, 2);
    assert.equal(knowledge.knowledge.worlds.length, 1);
    const lin = knowledge.knowledge.entities.find((item) => item.displayName === "林砚");
    const su = knowledge.knowledge.entities.find((item) => item.displayName === "苏乔");
    assert.ok(lin && su);
    assert.equal(lin.profileFields["身份"], "雾港调查员");
    assert.match(lin.cardRelativePath, /^knowledge\/generated\/characters\//u);
    assert.equal(knowledge.knowledge.worlds[0].name, "雾港");
    assert.equal(knowledge.knowledge.events.length, 2);
    assert.equal(knowledge.knowledge.diagnostics[0].code, "temporal_overlap");
    assert.ok(knowledge.knowledge.evidence.length > 0);
    assert.equal(knowledge.knowledge.events.find((item) => item.title === "收到密信").participantEntityIds.length, 2);
    const renamed = await client.request("knowledge.renameEntity", {
      entityId: lin.entityId,
      displayName: "林砚舟",
    });
    assert.equal(renamed.knowledge.operationCount, 1);
    const aliased = await client.request("knowledge.setEntityAliases", {
      entityId: lin.entityId,
      aliases: ["阿砚"],
    });
    assert.deepEqual(aliased.knowledge.entities.find((item) => item.entityId === lin.entityId).aliases, ["阿砚"]);
    const relation = aliased.knowledge.relations[0];
    const relationEdited = await client.request("knowledge.updateRelation", {
      relationId: relation.relationId,
      sourceEntityId: su.entityId,
      targetEntityId: lin.entityId,
      label: "保护对象",
    });
    assert.equal(relationEdited.knowledge.relations[0].label, "保护对象");

    const fieldEdited = await client.request("knowledge.updateCharacterField", {
      entityId: lin.entityId,
      field: "身份",
      value: "雾港首席调查员",
    });
    assert.equal(fieldEdited.knowledge.entities.find((item) => item.entityId === lin.entityId).profileFields["身份"], "雾港首席调查员");
    const fieldHidden = await client.request("knowledge.hideCharacterField", { entityId: lin.entityId, field: "身份" });
    assert.equal(fieldHidden.knowledge.entities.find((item) => item.entityId === lin.entityId).profileFields["身份"], undefined);
    assert.equal(fieldHidden.knowledge.entities.find((item) => item.entityId === lin.entityId).hiddenProfileFields["身份"], "雾港首席调查员");
    const fieldRestored = await client.request("knowledge.restoreCharacterField", { entityId: lin.entityId, field: "身份" });
    assert.equal(fieldRestored.knowledge.entities.find((item) => item.entityId === lin.entityId).profileFields["身份"], "雾港首席调查员");

    const generatedCard = await client.request("knowledge.openCard", {
      ownerKind: "character",
      ownerId: lin.entityId,
      mode: "generated",
    });
    assert.equal(generatedCard.card.readOnly, true);
    assert.match(generatedCard.card.content, /雾港首席调查员/u);
    const authorCard = await client.request("knowledge.openCard", {
      ownerKind: "character",
      ownerId: lin.entityId,
      mode: "author",
    });
    const authorSaved = await client.request("knowledge.saveAuthorCard", {
      ownerKind: "character",
      ownerId: lin.entityId,
      content: "# 作者补充\n\n不会被重建覆盖。\n",
      expectedRevision: authorCard.card.revision,
    });
    assert.match(authorSaved.card.content, /不会被重建覆盖/u);
    assert.notEqual(authorSaved.card.revision, authorCard.card.revision);
    await assert.rejects(client.request("knowledge.saveAuthorCard", {
      ownerKind: "character",
      ownerId: lin.entityId,
      content: "过期写入",
      expectedRevision: authorCard.card.revision,
    }), /已经变化/u);

    const worldId = knowledge.knowledge.worlds[0].worldId;
    const worldEdited = await client.request("knowledge.updateWorld", {
      worldId,
      name: "雾港城",
      category: "核心地点",
      description: "一座终年被浓雾笼罩的港城",
    });
    assert.equal(worldEdited.knowledge.worlds[0].name, "雾港城");
    const worldHidden = await client.request("knowledge.hideWorld", { worldId });
    assert.equal(worldHidden.knowledge.worlds.length, 0);
    assert.equal(worldHidden.knowledge.hiddenWorlds[0].worldId, worldId);
    const hiddenWorldAuthor = await client.request("knowledge.openCard", { ownerKind: "world", ownerId: worldId, mode: "author" });
    assert.equal(hiddenWorldAuthor.card.readOnly, false);
    const worldRestored = await client.request("knowledge.restoreWorld", { worldId });
    assert.equal(worldRestored.knowledge.worlds[0].category, "核心地点");
    assert.equal(worldRestored.knowledge.hiddenWorlds.length, 0);

    const eventId = knowledge.knowledge.events.find((item) => item.title === "收到密信").eventId;
    const otherEventId = knowledge.knowledge.events.find((item) => item.title === "前往灯塔").eventId;
    const eventLinksEdited = await client.request("knowledge.updateEventLinks", {
      eventId, participantEntityIds: [lin.entityId], worldIds: [],
    });
    assert.deepEqual(eventLinksEdited.knowledge.events.find((item) => item.eventId === eventId).participantEntityIds, [lin.entityId]);
    assert.deepEqual(eventLinksEdited.knowledge.events.find((item) => item.eventId === eventId).worldIds, []);
    const eventsReordered = await client.request("knowledge.reorderEvents", { eventIds: [otherEventId, eventId] });
    assert.deepEqual(eventsReordered.knowledge.events.map((item) => item.eventId), [otherEventId, eventId]);
    const eventEdited = await client.request("knowledge.updateEvent", {
      eventId, timeLabel: "深夜", title: "密信抵达", description: "两人在港口收到匿名密信",
    });
    assert.equal(eventEdited.knowledge.events.find((item) => item.eventId === eventId).title, "密信抵达");
    const eventHidden = await client.request("knowledge.hideEvent", { eventId });
    assert.equal(eventHidden.knowledge.events.length, 1);
    assert.equal(eventHidden.knowledge.hiddenEvents[0].eventId, eventId);
    const eventRestored = await client.request("knowledge.restoreEvent", { eventId });
    assert.equal(eventRestored.knowledge.events.find((item) => item.eventId === eventId).timeLabel, "深夜");
    assert.deepEqual(eventRestored.knowledge.events.map((item) => item.eventId), [otherEventId, eventId]);

    const curatedGraph = await client.request("graph.snapshot");
    assert.equal(curatedGraph.graph.nodes.find((item) => item.id === lin.entityId).name, "林砚舟");
    assert.equal(curatedGraph.graph.edges[0].source, su.entityId);
    const merged = await client.request("knowledge.mergeEntities", {
      sourceEntityId: lin.entityId,
      targetEntityId: su.entityId,
    });
    assert.equal(merged.knowledge.merges.length, 1);
    const split = await client.request("knowledge.unmergeEntity", { sourceEntityId: lin.entityId });
    assert.equal(split.knowledge.merges.length, 0);
    assert.equal(split.knowledge.entities.length, 2);
    const first = await client.request("manuscript.open", { chapterId: "chapter_0001" });
    assert.match(first.document.revision, /^v2:/);
    const saved = await client.request("manuscript.save", {
      chapterId: "chapter_0001",
      content: "# 第一章·潮声\n\n修改后的正文。\n",
      expectedRevision: first.document.revision,
      force: false,
    });
    assert.equal(saved.document.title, "第一章·潮声");
    const invalidated = await client.request("reconstruction.snapshot");
    assert.equal(invalidated.reconstruction.acceptedEntityCount, 0);
    assert.equal(invalidated.reconstruction.staleBatchCount, 1);
    await assert.rejects(
      client.request("manuscript.save", {
        chapterId: "chapter_0001",
        content: "过期写入",
        expectedRevision: first.document.revision,
        force: false,
      }),
      (error) => error?.name === "SidecarRpcError" && error.code === "REVISION_CONFLICT",
    );
  } finally {
    await client.stop();
    await rm(temporaryRoot, { recursive: true, force: true });
  }
});

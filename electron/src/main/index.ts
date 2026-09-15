import { existsSync } from "node:fs";
import { rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  type IpcMainInvokeEvent,
  type OpenDialogOptions,
} from "electron";
import type {
  AIResultEnvelope,
  AIStartInput,
  AIStatus,
  AITask,
  AppEvent,
  DocumentSnapshot,
  Handshake,
  ImportResult,
  KnowledgeCardDocument,
  KnowledgeSnapshot,
  ManuscriptImportPlan,
  ManuscriptExportResult,
  ManuscriptMutationResult,
  ManuscriptSnapshot,
  ManuscriptTrashSnapshot,
  MutationResult,
  OpenedProject,
  OpenedProjectV2,
  OperationResult,
  PreviewStatus,
  PreferencesPatch,
  PreferencesSnapshot,
  ProjectDocumentItem,
  ProjectSnapshot,
  ReconstructionBatch,
  ReconstructionSnapshot,
  ReconstructionTask,
  ReconstructionTaskStatus,
  RelationshipGraphSnapshot,
  RpcError,
  SaveDocumentInput,
  SaveManuscriptInput,
  TrashItem,
  TrashSnapshot,
} from "../shared/contracts.js";
import {
  SidecarClient,
  SidecarRpcError,
  type TransportEvent,
} from "./sidecar-client.js";
import {
  loadReleaseTrustPolicy,
  type ReleaseTrustPolicy,
} from "./release-trust.js";

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const electronRoot = path.resolve(currentDirectory, "../../..");
const repositoryRoot = path.resolve(electronRoot, "..");
const applicationRoot = app.isPackaged ? app.getAppPath() : electronRoot;
const rendererPath = path.join(applicationRoot, "dist", "renderer", "index.html");
const preloadPath = path.join(
  applicationRoot,
  "dist",
  "electron",
  "preload",
  "index.cjs",
);
const selfTestMode = process.argv.includes("--self-test");
const capturePreview = process.argv.includes("--capture-preview");
const performanceReportPath = process.argv
  .find((argument) => argument.startsWith("--performance-report="))
  ?.slice("--performance-report=".length);
const selfTestProjectPath = process.argv
  .find((argument) => argument.startsWith("--self-test-project="))
  ?.slice("--self-test-project=".length);
const expectedReleaseKeyId = process.argv
  .find((argument) => argument.startsWith("--expected-release-key-id="))
  ?.slice("--expected-release-key-id=".length);

// Automated preview runs must not contend with a developer's installed or
// already-running Electron instance. Chromium scopes its singleton lock and
// encrypted state to userData, so give each self-test process an isolated
// disposable directory under the operating-system temporary root.
if (selfTestMode) {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("disable-gpu-compositing");
  app.commandLine.appendSwitch("disable-software-rasterizer");
  app.setPath(
    "userData",
    path.join(app.getPath("temp"), `novalist-electron-self-test-${process.pid}`),
  );
}

let mainWindow: BrowserWindow | null = null;
let handshake: Handshake | null = null;
let releaseTrust: ReleaseTrustPolicy | null = null;
let activeProjectRoot: string | null = null;
let documentDirty = false;
let allowWindowClose = false;
let shutdownStarted = false;

const sidecar = new SidecarClient(resolveSidecarCommand());

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow === null) {
      return;
    }
    if (mainWindow.isMinimized()) {
      mainWindow.restore();
    }
    mainWindow.focus();
  });

  void app.whenReady().then(startDesktop);
}

async function startDesktop(): Promise<void> {
  try {
    releaseTrust = app.isPackaged
      ? loadReleaseTrustPolicy(applicationRoot)
      : { schemaVersion: 1, mode: "local-rehearsal", keyId: null, publicKeyPem: null };
    handshake = await sidecar.start();
    if (handshake.applicationVersion !== app.getVersion()) {
      throw new Error(
        `桌面端与 Sidecar 版本不一致（Electron ${app.getVersion()}，Sidecar ${handshake.applicationVersion}）。`,
      );
    }
  } catch (error) {
    dialog.showErrorBox("DeepSonder", publicError(error).message);
    app.quit();
    return;
  }
  registerIpcHandlers();
  sidecar.onEvent((event) => {
    const translated = translateEvent(event);
    if (translated !== null && mainWindow !== null && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("novalist:app-event", translated);
    }
  });
  createWindow();
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    title: "DeepSonder",
    width: 1440,
    height: 920,
    minWidth: 1040,
    minHeight: 680,
    backgroundColor: "#f3efe8",
    show: false,
    icon: app.isPackaged
      ? path.join(process.resourcesPath, "assets", "app_icon.ico")
      : path.join(repositoryRoot, "assets", "app_icon.ico"),
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      backgroundThrottling: !selfTestMode,
    },
  });
  mainWindow.removeMenu();
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url !== mainWindow?.webContents.getURL()) {
      event.preventDefault();
    }
  });
  mainWindow.once("ready-to-show", () => {
    if (!selfTestMode) {
      mainWindow?.show();
    }
  });
  if (selfTestMode) {
    mainWindow.webContents.once("did-finish-load", () => {
      void runSelfTest();
    });
    mainWindow.webContents.once(
      "did-fail-load",
      (_event, errorCode, errorDescription) => {
        console.error(
          `electron-preview-self-test: renderer failed (${errorCode}: ${errorDescription})`,
        );
        app.exit(1);
      },
    );
  }
  mainWindow.on("close", (event) => {
    if (!documentDirty || allowWindowClose) {
      return;
    }
    event.preventDefault();
    void confirmDiscard("退出应用").then((confirmed) => {
      if (confirmed && mainWindow !== null) {
        allowWindowClose = true;
        mainWindow.close();
      }
    });
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
    void shutdownAndQuit();
  });
  void mainWindow.loadFile(rendererPath);
}

function registerIpcHandlers(): void {
  ipcMain.handle("novalist:get-status", async (): Promise<OperationResult<PreviewStatus>> => {
    if (handshake === null) {
      return failure(new Error("Sidecar 尚未连接。"));
    }
    return success({ connected: true, handshake });
  });

  ipcMain.handle(
    "novalist:choose-project",
    async (event): Promise<OperationResult<OpenedProject | null>> => {
      const owner = ownerWindow(event);
      const options: OpenDialogOptions = {
        title: "打开 DeepSonder 项目",
        buttonLabel: "打开项目",
        properties: ["openDirectory"],
      };
      const selected = owner === null
        ? await dialog.showOpenDialog(options)
        : await dialog.showOpenDialog(owner, options);
      if (selected.canceled || selected.filePaths[0] === undefined) {
        return success(null);
      }
      return invoke(async () => {
        const response = await sidecar.request<{ opened: OpenedProject }>(
          "project.open",
          { path: selected.filePaths[0], remember: true },
        );
        activeProjectRoot = response.opened.project.root;
        return response.opened;
      });
    },
  );

  ipcMain.handle(
    "novalist:create-project",
    async (
      event,
      name: unknown,
      author: unknown,
    ): Promise<OperationResult<OpenedProject | null>> => {
      if (!isNonBlankString(name) || name.trim().length > 200) {
        return failure(new Error("项目名称不能为空，且不能超过 200 个字符。"));
      }
      if (typeof author !== "string" || author.length > 500) {
        return failure(new Error("作者名称无效。"));
      }
      const owner = ownerWindow(event);
      const options: OpenDialogOptions = {
        title: "选择新项目保存位置",
        buttonLabel: "在此创建",
        properties: ["openDirectory", "createDirectory"],
      };
      const selected = owner === null
        ? await dialog.showOpenDialog(options)
        : await dialog.showOpenDialog(owner, options);
      if (selected.canceled || selected.filePaths[0] === undefined) {
        return success(null);
      }
      return invoke(async () => {
        const response = await sidecar.request<{ opened: OpenedProject }>(
          "project.create",
          {
            parentDirectory: selected.filePaths[0],
            name: name.trim(),
            author: author.trim(),
            remember: true,
          },
        );
        if (!isOpenedProject(response.opened)) {
          throw new Error("Sidecar 返回了无效的新项目描述。");
        }
        activeProjectRoot = response.opened.project.root;
        return response.opened;
      });
    },
  );

  ipcMain.handle(
    "novalist:restore-last-project",
    async (): Promise<OperationResult<OpenedProject | null>> =>
      invoke(async () => {
        const response = await sidecar.request<{ opened: OpenedProject | null }>(
          "project.restoreLast",
        );
        if (response.opened !== null && !isOpenedProject(response.opened)) {
          throw new Error("Sidecar 返回了无效的最近项目描述。");
        }
        activeProjectRoot = response.opened?.project.root ?? null;
        return response.opened;
      }),
  );

  ipcMain.handle(
    "novalist:choose-scan-manuscript",
    async (
      event,
      sourceType: unknown,
    ): Promise<OperationResult<ManuscriptImportPlan | null>> => {
      if (sourceType !== "file" && sourceType !== "directory") {
        return failure(new Error("正文来源类型无效。"));
      }
      const owner = ownerWindow(event);
      const options: OpenDialogOptions = {
        title: sourceType === "file" ? "选择正文文件" : "选择旧项目或正文目录",
        buttonLabel: "扫描正文",
        properties: sourceType === "file" ? ["openFile"] : ["openDirectory"],
        ...(sourceType === "file" ? {
          filters: [{ name: "正文", extensions: ["md", "markdown", "txt"] }],
        } : {}),
      };
      const selected = owner === null
        ? await dialog.showOpenDialog(options)
        : await dialog.showOpenDialog(owner, options);
      if (selected.canceled || selected.filePaths[0] === undefined) return success(null);
      return invoke(async () => {
        const response = await sidecar.request<{ plan: ManuscriptImportPlan }>(
          "manuscript.scanImport",
          { sourcePath: selected.filePaths[0] },
        );
        if (!isManuscriptImportPlan(response.plan)) {
          throw new Error("Sidecar 返回了无效的正文导入计划。");
        }
        return response.plan;
      });
    },
  );

  ipcMain.handle(
    "novalist:create-project-v2",
    async (
      event,
      name: unknown,
      author: unknown,
      planDigest: unknown,
    ): Promise<OperationResult<OpenedProjectV2 | null>> => {
      if (!isNonBlankString(name) || name.length > 200 || typeof author !== "string" || author.length > 500) {
        return failure(new Error("v2 项目名称或作者无效。"));
      }
      if (typeof planDigest !== "string" || planDigest.length > 100) {
        return failure(new Error("正文导入计划摘要无效。"));
      }
      const owner = ownerWindow(event);
      const options: OpenDialogOptions = {
        title: "选择新项目保存位置",
        buttonLabel: "在此创建",
        properties: ["openDirectory", "createDirectory"],
      };
      const selected = owner === null
        ? await dialog.showOpenDialog(options)
        : await dialog.showOpenDialog(owner, options);
      if (selected.canceled || selected.filePaths[0] === undefined) return success(null);
      return invoke(async () => {
        const response = await sidecar.request<{ opened: OpenedProjectV2 }>(
          "project.createV2",
          {
            parentDirectory: selected.filePaths[0],
            name: name.trim(),
            author: author.trim(),
            planDigest,
          },
        );
        if (!isOpenedProjectV2(response.opened)) throw new Error("Sidecar 返回了无效的 v2 项目。");
        activeProjectRoot = response.opened.root;
        return response.opened;
      });
    },
  );

  ipcMain.handle(
    "novalist:choose-project-v2",
    async (event): Promise<OperationResult<OpenedProjectV2 | null>> => {
      const owner = ownerWindow(event);
      const options: OpenDialogOptions = {
        title: "打开 DeepSonder v2 项目",
        buttonLabel: "打开项目",
        properties: ["openDirectory"],
      };
      const selected = owner === null
        ? await dialog.showOpenDialog(options)
        : await dialog.showOpenDialog(owner, options);
      if (selected.canceled || selected.filePaths[0] === undefined) return success(null);
      return invoke(async () => {
        const response = await sidecar.request<{ opened: OpenedProjectV2 }>(
          "project.openV2", { path: selected.filePaths[0] },
        );
        if (!isOpenedProjectV2(response.opened)) throw new Error("Sidecar 返回了无效的 v2 项目。");
        activeProjectRoot = response.opened.root;
        return response.opened;
      });
    },
  );

  ipcMain.handle(
    "novalist:get-manuscript-snapshot",
    async (): Promise<OperationResult<ManuscriptSnapshot>> => invoke(async () => {
      const response = await sidecar.request<{ snapshot: ManuscriptSnapshot }>("manuscript.snapshot");
      if (!isManuscriptSnapshot(response.snapshot)) throw new Error("Sidecar 返回了无效的正文索引。");
      return response.snapshot;
    }),
  );

  ipcMain.handle(
    "novalist:open-manuscript",
    async (_event, chapterId: unknown): Promise<OperationResult<DocumentSnapshot>> => {
      if (!isNonBlankString(chapterId) || chapterId.length > 100) return failure(new Error("章节 ID 无效。"));
      return invoke(async () => {
        const response = await sidecar.request<{ document: DocumentSnapshot }>(
          "manuscript.open", { chapterId },
        );
        documentDirty = false;
        return response.document;
      });
    },
  );

  ipcMain.handle(
    "novalist:save-manuscript",
    async (_event, input: unknown): Promise<OperationResult<DocumentSnapshot>> => {
      if (!isSaveManuscriptInput(input)) return failure(new Error("正文保存参数无效。"));
      return invoke(async () => {
        const response = await sidecar.request<{ document: DocumentSnapshot }>(
          "manuscript.save", { ...input },
        );
        documentDirty = false;
        return response.document;
      });
    },
  );

  ipcMain.handle(
    "novalist:create-manuscript",
    async (_event, title: unknown, afterChapterId: unknown): Promise<OperationResult<ManuscriptMutationResult>> => {
      if (!isNonBlankString(title) || title.length > 200 ||
          (afterChapterId !== undefined && (!isNonBlankString(afterChapterId) || afterChapterId.length > 100))) {
        return failure(new Error("新章节参数无效。"));
      }
      return invoke(async () => {
        const response = await sidecar.request<ManuscriptMutationResult>("manuscript.create", {
          title: title.trim(),
          ...(afterChapterId === undefined ? {} : { afterChapterId }),
        });
        if (!isManuscriptMutationResult(response)) throw new Error("Sidecar 返回了无效的章节创建结果。");
        documentDirty = false;
        return response;
      });
    },
  );

  ipcMain.handle(
    "novalist:rename-manuscript",
    async (_event, chapterId: unknown, title: unknown, expectedRevision: unknown): Promise<OperationResult<ManuscriptMutationResult>> => {
      if (!isNonBlankString(chapterId) || chapterId.length > 100 || !isNonBlankString(title) || title.length > 200 || !isNonBlankString(expectedRevision)) {
        return failure(new Error("章节重命名参数无效。"));
      }
      return invoke(async () => {
        const response = await sidecar.request<ManuscriptMutationResult>("manuscript.rename", {
          chapterId, title: title.trim(), expectedRevision,
        });
        if (!isManuscriptMutationResult(response)) throw new Error("Sidecar 返回了无效的章节重命名结果。");
        documentDirty = false;
        return response;
      });
    },
  );

  ipcMain.handle(
    "novalist:reorder-manuscript",
    async (_event, chapterIds: unknown): Promise<OperationResult<ManuscriptMutationResult>> => {
      if (!Array.isArray(chapterIds) || chapterIds.length > 10_000 ||
          !chapterIds.every((value) => isNonBlankString(value) && value.length <= 100)) {
        return failure(new Error("章节排序参数无效。"));
      }
      return invoke(async () => {
        const response = await sidecar.request<ManuscriptMutationResult>("manuscript.reorder", { chapterIds });
        if (!isManuscriptMutationResult(response)) throw new Error("Sidecar 返回了无效的章节排序结果。");
        return response;
      });
    },
  );

  ipcMain.handle(
    "novalist:delete-manuscript",
    async (_event, chapterId: unknown): Promise<OperationResult<ManuscriptMutationResult>> => {
      if (!isNonBlankString(chapterId) || chapterId.length > 100) return failure(new Error("章节 ID 无效。"));
      return invoke(async () => {
        const response = await sidecar.request<ManuscriptMutationResult>("manuscript.delete", { chapterId });
        if (!isManuscriptMutationResult(response)) throw new Error("Sidecar 返回了无效的章节删除结果。");
        documentDirty = false;
        return response;
      });
    },
  );

  ipcMain.handle(
    "novalist:get-manuscript-trash",
    async (): Promise<OperationResult<ManuscriptTrashSnapshot>> => invoke(async () => {
      const response = await sidecar.request<{ trash: ManuscriptTrashSnapshot }>("manuscript.trashList");
      if (!isManuscriptTrashSnapshot(response.trash)) throw new Error("Sidecar 返回了无效的正文回收站。");
      return response.trash;
    }),
  );

  ipcMain.handle(
    "novalist:restore-manuscript-trash",
    async (_event, trashId: unknown): Promise<OperationResult<ManuscriptMutationResult>> => {
      if (!isNonBlankString(trashId) || trashId.length > 100) return failure(new Error("回收站条目 ID 无效。"));
      return invoke(async () => {
        const response = await sidecar.request<ManuscriptMutationResult>("manuscript.trashRestore", { trashId });
        if (!isManuscriptMutationResult(response)) throw new Error("Sidecar 返回了无效的章节恢复结果。");
        documentDirty = false;
        return response;
      });
    },
  );

  ipcMain.handle(
    "novalist:delete-manuscript-trash-forever",
    async (_event, trashId: unknown): Promise<OperationResult<ManuscriptTrashSnapshot>> => {
      if (!isNonBlankString(trashId) || trashId.length > 100) return failure(new Error("回收站条目 ID 无效。"));
      return invoke(async () => {
        const response = await sidecar.request<{ trash: ManuscriptTrashSnapshot }>("manuscript.trashDeleteForever", { trashId });
        if (!isManuscriptTrashSnapshot(response.trash)) throw new Error("Sidecar 返回了无效的正文回收站。");
        return response.trash;
      });
    },
  );

  ipcMain.handle(
    "novalist:append-manuscript",
    async (_event, planDigest: unknown, afterChapterId: unknown): Promise<OperationResult<ManuscriptMutationResult>> => {
      if (!isNonBlankString(planDigest) || planDigest.length > 100 ||
          (afterChapterId !== undefined && (!isNonBlankString(afterChapterId) || afterChapterId.length > 100))) {
        return failure(new Error("正文追加导入参数无效。"));
      }
      return invoke(async () => {
        const response = await sidecar.request<ManuscriptMutationResult>("manuscript.appendImport", {
          planDigest,
          ...(afterChapterId === undefined ? {} : { afterChapterId }),
        });
        if (!isManuscriptMutationResult(response)) throw new Error("Sidecar 返回了无效的正文追加结果。");
        documentDirty = false;
        return response;
      });
    },
  );

  ipcMain.handle(
    "novalist:export-manuscript",
    async (event, format: unknown): Promise<OperationResult<ManuscriptExportResult | null>> => {
      if (format !== "md" && format !== "txt") return failure(new Error("正文导出格式无效。"));
      if (activeProjectRoot === null) return failure(new Error("当前没有打开的项目。"));
      const extension = format;
      const options = {
        title: "导出全书",
        buttonLabel: "导出",
        defaultPath: path.join(activeProjectRoot, `${path.basename(activeProjectRoot)}-全书.${extension}`),
        filters: [{ name: format === "md" ? "Markdown" : "纯文本", extensions: [extension] }],
        properties: ["showOverwriteConfirmation"] as Array<"showOverwriteConfirmation">,
      };
      const owner = ownerWindow(event);
      const selected = owner === null
        ? await dialog.showSaveDialog(options)
        : await dialog.showSaveDialog(owner, options);
      if (selected.canceled || selected.filePath === undefined) return success(null);
      return invoke(async () => {
        const response = await sidecar.request<{ exported: ManuscriptExportResult }>("manuscript.export", {
          destination: selected.filePath,
          format,
        });
        if (!isManuscriptExportResult(response.exported)) throw new Error("Sidecar 返回了无效的正文导出结果。");
        return response.exported;
      });
    },
  );

  ipcMain.handle(
    "novalist:get-reconstruction-snapshot",
    async (): Promise<OperationResult<ReconstructionSnapshot>> => invoke(async () => {
      const response = await sidecar.request<{ reconstruction: ReconstructionSnapshot }>("reconstruction.snapshot");
      if (!isReconstructionSnapshot(response.reconstruction)) throw new Error("Sidecar 返回了无效的重建状态。");
      return response.reconstruction;
    }),
  );

  ipcMain.handle(
    "novalist:generate-reconstruction",
    async (): Promise<OperationResult<ReconstructionBatch>> => invoke(async () => {
      const response = await sidecar.request<{ batch: ReconstructionBatch }>("reconstruction.generate");
      if (!isReconstructionBatch(response.batch)) throw new Error("Sidecar 返回了无效的候选批次。");
      return response.batch;
    }),
  );

  ipcMain.handle(
    "novalist:get-reconstruction-task-status",
    async (): Promise<OperationResult<ReconstructionTaskStatus>> => invoke(async () => {
      const response = await sidecar.request<{ reconstructionTask: ReconstructionTaskStatus }>("reconstruction.taskStatus");
      if (!isReconstructionTaskStatus(response.reconstructionTask)) throw new Error("Sidecar 返回了无效的正文识别任务状态。");
      return response.reconstructionTask;
    }),
  );

  ipcMain.handle(
    "novalist:start-reconstruction",
    async (_event, mode: unknown, remoteConsent: unknown): Promise<OperationResult<ReconstructionTask>> => {
      if (!["local", "dsh"].includes(String(mode)) || typeof remoteConsent !== "boolean" || (mode === "dsh" && !remoteConsent)) {
        return failure(new Error("正文识别模式或 DSH 发送授权无效。"));
      }
      return invoke(async () => {
      const response = await sidecar.request<{ task: ReconstructionTask }>("reconstruction.start", { mode, remoteConsent });
      if (!isReconstructionTask(response.task)) throw new Error("Sidecar 返回了无效的正文识别任务。");
      return response.task;
      });
    },
  );

  ipcMain.handle(
    "novalist:cancel-reconstruction",
    async (_event, taskId: unknown): Promise<OperationResult<ReconstructionTask>> => {
      if (!isNonBlankString(taskId) || taskId.length > 100) return failure(new Error("正文识别任务 ID 无效。"));
      return invoke(async () => {
        const response = await sidecar.request<{ task: ReconstructionTask }>("reconstruction.cancel", { taskId });
        if (!isReconstructionTask(response.task)) throw new Error("Sidecar 返回了无效的取消状态。");
        return response.task;
      });
    },
  );

  ipcMain.handle(
    "novalist:get-reconstruction-batch",
    async (_event, batchId: unknown): Promise<OperationResult<ReconstructionBatch>> => {
      if (!isNonBlankString(batchId) || batchId.length > 100) return failure(new Error("候选批次 ID 无效。"));
      return invoke(async () => {
        const response = await sidecar.request<{ batch: ReconstructionBatch }>("reconstruction.batch", { batchId });
        if (!isReconstructionBatch(response.batch)) throw new Error("Sidecar 返回了无效的候选批次。");
        return response.batch;
      });
    },
  );

  ipcMain.handle(
    "novalist:review-reconstruction",
    async (_event, batchId: unknown, proposalId: unknown, decision: unknown): Promise<OperationResult<ReconstructionBatch>> => {
      if (!isNonBlankString(batchId) || batchId.length > 100 || !isNonBlankString(proposalId) || proposalId.length > 100 || !["accepted", "rejected"].includes(String(decision))) {
        return failure(new Error("候选审核参数无效。"));
      }
      return invoke(async () => {
        const response = await sidecar.request<{ batch: ReconstructionBatch }>(
          "reconstruction.review", { batchId, proposalId, decision },
        );
        if (!isReconstructionBatch(response.batch)) throw new Error("Sidecar 返回了无效的审核结果。");
        return response.batch;
      });
    },
  );

  ipcMain.handle(
    "novalist:review-reconstruction-many",
    async (_event, batchId: unknown, decisions: unknown): Promise<OperationResult<ReconstructionBatch>> => {
      if (!validKnowledgeId(batchId) || !Array.isArray(decisions) || decisions.length < 1 || decisions.length > 200 ||
          !decisions.every((item) => isRecord(item) && Object.keys(item).length === 2 &&
            validKnowledgeId(item.proposalId) && ["accepted", "rejected"].includes(String(item.decision)))) {
        return failure(new Error("批量候选审核参数无效。"));
      }
      return invoke(async () => {
        const response = await sidecar.request<{ batch: ReconstructionBatch }>(
          "reconstruction.reviewMany", { batchId, decisions },
        );
        if (!isReconstructionBatch(response.batch)) throw new Error("Sidecar 返回了无效的批量审核结果。");
        return response.batch;
      });
    },
  );

  ipcMain.handle(
    "novalist:get-knowledge",
    async (): Promise<OperationResult<KnowledgeSnapshot>> => invoke(async () => {
      const response = await sidecar.request<{ knowledge: KnowledgeSnapshot }>("knowledge.snapshot");
      if (!isKnowledgeSnapshot(response.knowledge)) throw new Error("Sidecar 返回了无效的知识整理状态。");
      return response.knowledge;
    }),
  );

  ipcMain.handle(
    "novalist:rename-knowledge-entity",
    async (_event, entityId: unknown, displayName: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(entityId) || !validKnowledgeLabel(displayName)) return failure(new Error("人物重命名参数无效。"));
      return invokeKnowledge("knowledge.renameEntity", { entityId, displayName: displayName.trim() });
    },
  );

  ipcMain.handle(
    "novalist:set-knowledge-aliases",
    async (_event, entityId: unknown, aliases: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(entityId) || !Array.isArray(aliases) || aliases.length > 20 || !aliases.every(validKnowledgeLabel)) {
        return failure(new Error("人物别名参数无效。"));
      }
      return invokeKnowledge("knowledge.setEntityAliases", { entityId, aliases: aliases.map((item) => item.trim()) });
    },
  );

  ipcMain.handle(
    "novalist:merge-knowledge-entities",
    async (_event, sourceEntityId: unknown, targetEntityId: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(sourceEntityId) || !validKnowledgeId(targetEntityId) || sourceEntityId === targetEntityId) {
        return failure(new Error("人物合并参数无效。"));
      }
      return invokeKnowledge("knowledge.mergeEntities", { sourceEntityId, targetEntityId });
    },
  );

  ipcMain.handle(
    "novalist:unmerge-knowledge-entity",
    async (_event, sourceEntityId: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(sourceEntityId)) return failure(new Error("人物拆分参数无效。"));
      return invokeKnowledge("knowledge.unmergeEntity", { sourceEntityId });
    },
  );

  ipcMain.handle(
    "novalist:update-knowledge-relation",
    async (_event, relationId: unknown, sourceEntityId: unknown, targetEntityId: unknown, label: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(relationId) || !validKnowledgeId(sourceEntityId) || !validKnowledgeId(targetEntityId) || sourceEntityId === targetEntityId || !validKnowledgeLabel(label)) {
        return failure(new Error("关系编辑参数无效。"));
      }
      return invokeKnowledge("knowledge.updateRelation", { relationId, sourceEntityId, targetEntityId, label: label.trim() });
    },
  );

  ipcMain.handle(
    "novalist:delete-knowledge-relation",
    async (_event, relationId: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(relationId)) return failure(new Error("关系删除参数无效。"));
      return invokeKnowledge("knowledge.deleteRelation", { relationId });
    },
  );

  ipcMain.handle(
    "novalist:update-knowledge-character-field",
    async (_event, entityId: unknown, field: unknown, value: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(entityId) || !validCharacterField(field) || !validKnowledgeText(value)) return failure(new Error("角色字段编辑参数无效。"));
      return invokeKnowledge("knowledge.updateCharacterField", { entityId, field, value: value.trim() });
    },
  );
  ipcMain.handle(
    "novalist:hide-knowledge-character-field",
    async (_event, entityId: unknown, field: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(entityId) || !validCharacterField(field)) return failure(new Error("角色字段隐藏参数无效。"));
      return invokeKnowledge("knowledge.hideCharacterField", { entityId, field });
    },
  );
  ipcMain.handle(
    "novalist:restore-knowledge-character-field",
    async (_event, entityId: unknown, field: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(entityId) || !validCharacterField(field)) return failure(new Error("角色字段恢复参数无效。"));
      return invokeKnowledge("knowledge.restoreCharacterField", { entityId, field });
    },
  );
  ipcMain.handle(
    "novalist:update-knowledge-world",
    async (_event, worldId: unknown, name: unknown, category: unknown, description: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(worldId) || !validKnowledgeLabel(name) || !validKnowledgeLabel(category) || !validKnowledgeText(description)) return failure(new Error("世界观编辑参数无效。"));
      return invokeKnowledge("knowledge.updateWorld", { worldId, name: name.trim(), category: category.trim(), description: description.trim() });
    },
  );
  ipcMain.handle(
    "novalist:hide-knowledge-world",
    async (_event, worldId: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(worldId)) return failure(new Error("世界观隐藏参数无效。"));
      return invokeKnowledge("knowledge.hideWorld", { worldId });
    },
  );
  ipcMain.handle(
    "novalist:restore-knowledge-world",
    async (_event, worldId: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(worldId)) return failure(new Error("世界观恢复参数无效。"));
      return invokeKnowledge("knowledge.restoreWorld", { worldId });
    },
  );
  ipcMain.handle(
    "novalist:update-knowledge-event",
    async (_event, eventId: unknown, timeLabel: unknown, title: unknown, description: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(eventId) || !validEventTime(timeLabel) || !validKnowledgeLabel(title) || !validKnowledgeText(description)) return failure(new Error("事件编辑参数无效。"));
      return invokeKnowledge("knowledge.updateEvent", { eventId, timeLabel: timeLabel.trim(), title: title.trim(), description: description.trim() });
    },
  );
  ipcMain.handle(
    "novalist:update-knowledge-event-links",
    async (_event, eventId: unknown, participantEntityIds: unknown, worldIds: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(eventId) || !isUniqueKnowledgeIdArray(participantEntityIds, 200) || !isUniqueKnowledgeIdArray(worldIds, 100)) {
        return failure(new Error("事件语义连接参数无效。"));
      }
      return invokeKnowledge("knowledge.updateEventLinks", { eventId, participantEntityIds, worldIds });
    },
  );
  ipcMain.handle(
    "novalist:reorder-knowledge-events",
    async (_event, eventIds: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!isUniqueKnowledgeIdArray(eventIds, 1000)) return failure(new Error("事件排序参数无效。"));
      return invokeKnowledge("knowledge.reorderEvents", { eventIds });
    },
  );
  ipcMain.handle(
    "novalist:hide-knowledge-event",
    async (_event, eventId: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(eventId)) return failure(new Error("事件隐藏参数无效。"));
      return invokeKnowledge("knowledge.hideEvent", { eventId });
    },
  );
  ipcMain.handle(
    "novalist:restore-knowledge-event",
    async (_event, eventId: unknown): Promise<OperationResult<KnowledgeSnapshot>> => {
      if (!validKnowledgeId(eventId)) return failure(new Error("事件恢复参数无效。"));
      return invokeKnowledge("knowledge.restoreEvent", { eventId });
    },
  );
  ipcMain.handle(
    "novalist:open-knowledge-card",
    async (_event, ownerKind: unknown, ownerId: unknown, mode: unknown): Promise<OperationResult<KnowledgeCardDocument>> => {
      if (!["character", "world"].includes(String(ownerKind)) || !validKnowledgeId(ownerId) || !["generated", "author"].includes(String(mode))) return failure(new Error("知识卡打开参数无效。"));
      return invoke(async () => {
        const response = await sidecar.request<{ card: KnowledgeCardDocument }>("knowledge.openCard", { ownerKind, ownerId, mode });
        if (!isKnowledgeCard(response.card)) throw new Error("Sidecar 返回了无效的知识卡。");
        return response.card;
      });
    },
  );
  ipcMain.handle(
    "novalist:save-knowledge-author-card",
    async (_event, ownerKind: unknown, ownerId: unknown, content: unknown, expectedRevision: unknown): Promise<OperationResult<KnowledgeCardDocument>> => {
      if (!["character", "world"].includes(String(ownerKind)) || !validKnowledgeId(ownerId) || typeof content !== "string" || content.length > 200_000 || !isNonBlankString(expectedRevision) || expectedRevision.length > 100) return failure(new Error("作者卡保存参数无效。"));
      return invoke(async () => {
        const response = await sidecar.request<{ card: KnowledgeCardDocument }>("knowledge.saveAuthorCard", { ownerKind, ownerId, content, expectedRevision });
        if (!isKnowledgeCard(response.card) || response.card.readOnly) throw new Error("Sidecar 返回了无效的作者卡。");
        return response.card;
      });
    },
  );

  ipcMain.handle(
    "novalist:reopen-reconstruction-proposal",
    async (_event, batchId: unknown, proposalId: unknown): Promise<OperationResult<ReconstructionBatch>> => {
      if (!validKnowledgeId(batchId) || !validKnowledgeId(proposalId)) return failure(new Error("重新审核参数无效。"));
      return invoke(async () => {
        const response = await sidecar.request<{ batch: ReconstructionBatch }>("knowledge.reopenProposal", { batchId, proposalId });
        if (!isReconstructionBatch(response.batch)) throw new Error("Sidecar 返回了无效的候选批次。");
        return response.batch;
      });
    },
  );

  ipcMain.handle(
    "novalist:get-preferences",
    async (): Promise<OperationResult<PreferencesSnapshot>> =>
      invoke(async () => {
        const response = await sidecar.request<{ preferences: PreferencesSnapshot }>(
          "preferences.get",
        );
        if (!isPreferencesSnapshot(response.preferences)) {
          throw new Error("Sidecar 返回了无效的界面偏好设置。");
        }
        return response.preferences;
      }),
  );

  ipcMain.handle(
    "novalist:update-preferences",
    async (
      _event,
      patch: unknown,
    ): Promise<OperationResult<PreferencesSnapshot>> => {
      if (!isPreferencesPatch(patch)) {
        return failure(new Error("界面偏好设置参数无效。"));
      }
      return invoke(async () => {
        const response = await sidecar.request<{ preferences: PreferencesSnapshot }>(
          "preferences.update",
          { patch: preferencesPatchDto(patch) },
        );
        if (!isPreferencesSnapshot(response.preferences)) {
          throw new Error("Sidecar 返回了无效的界面偏好设置。");
        }
        return response.preferences;
      });
    },
  );

  ipcMain.handle(
    "novalist:close-project",
    async (): Promise<OperationResult<{ closed: boolean }>> =>
      invoke(async () => {
        const result = await sidecar.request<{ closed: boolean }>("project.close");
        activeProjectRoot = null;
        documentDirty = false;
        return result;
      }),
  );

  ipcMain.handle(
    "novalist:get-project-snapshot",
    async (): Promise<OperationResult<ProjectSnapshot>> =>
      invoke(async () => {
        const result = await sidecar.request<{ snapshot: ProjectSnapshot }>(
          "project.snapshot",
        );
        return result.snapshot;
      }),
  );

  ipcMain.handle(
    "novalist:get-relationship-graph",
    async (): Promise<OperationResult<RelationshipGraphSnapshot>> =>
      invoke(async () => {
        const result = await sidecar.request<{ graph: RelationshipGraphSnapshot }>(
          "graph.snapshot",
        );
        if (!isRelationshipGraphSnapshot(result.graph)) {
          throw new Error("Sidecar 返回了无效的关系图谱。");
        }
        return result.graph;
      }),
  );

  ipcMain.handle(
    "novalist:choose-document",
    async (
      event,
      category: unknown,
    ): Promise<OperationResult<DocumentSnapshot | null>> => {
      if (typeof category !== "string") {
        return failure(new Error("文档分类无效。"));
      }
      const owner = ownerWindow(event);
      const options: OpenDialogOptions = {
        title: "打开项目 Markdown 文档",
        buttonLabel: "打开文档",
        ...(activeProjectRoot === null ? {} : { defaultPath: activeProjectRoot }),
        filters: [{ name: "Markdown", extensions: ["md"] }],
        properties: ["openFile"],
      };
      const selected = owner === null
        ? await dialog.showOpenDialog(options)
        : await dialog.showOpenDialog(owner, options);
      if (selected.canceled || selected.filePaths[0] === undefined) {
        return success(null);
      }
      return openDocument(selected.filePaths[0], category);
    },
  );

  ipcMain.handle(
    "novalist:open-document",
    async (
      _event,
      pathValue: unknown,
      category: unknown,
    ): Promise<OperationResult<DocumentSnapshot>> => {
      if (typeof pathValue !== "string" || typeof category !== "string") {
        return failure(new Error("文档参数无效。"));
      }
      return openDocument(pathValue, category);
    },
  );

  ipcMain.handle(
    "novalist:save-document",
    async (
      _event,
      input: unknown,
    ): Promise<OperationResult<DocumentSnapshot>> => {
      if (!isSaveDocumentInput(input)) {
        return failure(new Error("保存参数无效。"));
      }
      return invoke(async () => {
        const result = await sidecar.request<{ document: DocumentSnapshot }>(
          "document.save",
          { ...input },
        );
        documentDirty = false;
        return result.document;
      });
    },
  );

  ipcMain.handle(
    "novalist:create-chapter",
    async (_event, title: unknown, chapterId: unknown): Promise<OperationResult<MutationResult>> => {
      if (!isNonBlankString(title) || !isNonBlankString(chapterId)) {
        return failure(new Error("章节标题和文件标识不能为空。"));
      }
      return invoke(() => sidecar.request<MutationResult>("document.createChapter", { title, chapterId }));
    },
  );

  ipcMain.handle(
    "novalist:create-canon-entry",
    async (_event, kind: unknown, title: unknown): Promise<OperationResult<MutationResult>> => {
      if (!isCanonKind(kind) || !isNonBlankString(title)) {
        return failure(new Error("故事资料类型或名称无效。"));
      }
      return invoke(() => sidecar.request<MutationResult>("document.createCanonEntry", { kind, title }));
    },
  );

  ipcMain.handle(
    "novalist:create-timeline",
    async (): Promise<OperationResult<MutationResult>> =>
      invoke(() => sidecar.request<MutationResult>("document.createTimeline")),
  );

  ipcMain.handle(
    "novalist:import-markdown",
    async (event): Promise<OperationResult<ImportResult | null>> => {
      const owner = ownerWindow(event);
      const options: OpenDialogOptions = {
        title: "导入 Markdown 为章节",
        buttonLabel: "导入章节",
        filters: [{ name: "Markdown", extensions: ["md", "markdown"] }],
        properties: ["openFile", "multiSelections"],
      };
      const selected = owner === null
        ? await dialog.showOpenDialog(options)
        : await dialog.showOpenDialog(owner, options);
      if (selected.canceled || selected.filePaths.length === 0) {
        return success(null);
      }
      return invoke(() => sidecar.request<ImportResult>("document.importMarkdown", { sources: selected.filePaths }));
    },
  );

  ipcMain.handle(
    "novalist:delete-document",
    async (event, item: unknown): Promise<OperationResult<MutationResult | null>> => {
      if (!isProjectDocumentItem(item) || !isDeletableKind(item.kind) || item.protected) {
        return failure(new Error("该项目资料不能删除。"));
      }
      const owner = ownerWindow(event);
      if (owner === null) {
        return success(null);
      }
      const answer = await dialog.showMessageBox(owner, {
        type: "warning",
        title: "移入项目回收站",
        message: `要删除“${item.title}”吗？`,
        detail: "资料将移入当前项目的回收站，可以稍后恢复。",
        buttons: ["取消", "移入回收站"],
        defaultId: 0,
        cancelId: 0,
        noLink: true,
      });
      if (answer.response !== 1) {
        return success(null);
      }
      return invoke(() => sidecar.request<MutationResult>("document.delete", {
        kind: item.kind,
        itemId: item.id,
        path: item.path,
      }));
    },
  );

  ipcMain.handle(
    "novalist:set-system-importance",
    async (_event, pathValue: unknown, importance: unknown): Promise<OperationResult<ProjectSnapshot>> => {
      if (typeof pathValue !== "string" || (importance !== "core" && importance !== "non_core")) {
        return failure(new Error("体系重要性参数无效。"));
      }
      return invoke(async () => {
        const result = await sidecar.request<{ snapshot: ProjectSnapshot }>(
          "project.setSystemImportance",
          { path: pathValue, importance },
        );
        return result.snapshot;
      });
    },
  );

  ipcMain.handle(
    "novalist:get-trash",
    async (): Promise<OperationResult<TrashSnapshot>> =>
      invoke(async () => {
        const result = await sidecar.request<{ trash: TrashSnapshot }>("trash.list");
        return result.trash;
      }),
  );

  ipcMain.handle(
    "novalist:restore-trash",
    async (_event, kind: unknown, trashId: unknown, conflictPolicy: unknown) => {
      if (!isTrashKind(kind) || !isNonBlankString(trashId) || !isConflictPolicy(conflictPolicy)) {
        return failure(new Error("回收站恢复参数无效。"));
      }
      return invoke(() => sidecar.request("trash.restore", { kind, trashId, conflictPolicy }));
    },
  );

  ipcMain.handle(
    "novalist:delete-trash-forever",
    async (event, item: unknown): Promise<OperationResult<TrashSnapshot | null>> => {
      if (!isTrashItem(item)) {
        return failure(new Error("回收站条目无效。"));
      }
      const owner = ownerWindow(event);
      if (owner === null) {
        return success(null);
      }
      const answer = await dialog.showMessageBox(owner, {
        type: "warning",
        title: "永久删除资料",
        message: `永久删除“${item.title}”？`,
        detail: "此操作不可撤销，也无法从项目回收站恢复。",
        buttons: ["取消", "永久删除"],
        defaultId: 0,
        cancelId: 0,
        noLink: true,
      });
      if (answer.response !== 1) {
        return success(null);
      }
      return invoke(async () => {
        const result = await sidecar.request<{ trash: TrashSnapshot }>("trash.deleteForever", {
          kind: item.kind,
          trashId: item.trashId,
        });
        return result.trash;
      });
    },
  );

  ipcMain.handle(
    "novalist:ai-status",
    async (): Promise<OperationResult<AIStatus>> => invoke(async () => {
      const result = await sidecar.request<{ ai: AIStatus }>("ai.status");
      return result.ai;
    }),
  );

  ipcMain.handle(
    "novalist:ai-start",
    async (_event, input: unknown): Promise<OperationResult<AITask>> => {
      if (!isAIStartInput(input)) return failure(new Error("AI 任务参数无效。"));
      return invoke(async () => {
        const result = await sidecar.request<{ task: AITask }>("ai.start", { ...input });
        return result.task;
      });
    },
  );

  ipcMain.handle(
    "novalist:ai-cancel",
    async (_event, taskId: unknown): Promise<OperationResult<AITask>> => {
      if (!isNonBlankString(taskId)) return failure(new Error("AI 任务标识无效。"));
      return invoke(async () => {
        const result = await sidecar.request<{ task: AITask }>("ai.cancel", { taskId });
        return result.task;
      });
    },
  );

  ipcMain.handle(
    "novalist:ai-result",
    async (_event, taskId: unknown): Promise<OperationResult<AIResultEnvelope>> => {
      if (!isNonBlankString(taskId)) return failure(new Error("AI 任务标识无效。"));
      return invoke(async () => {
        const result = await sidecar.request<unknown>("ai.result", { taskId });
        if (!isAIResultEnvelope(result)) throw new Error("Sidecar 返回了无效的 AI 审阅结果。");
        return result;
      });
    },
  );

  ipcMain.handle(
    "novalist:ai-discard",
    async (_event, taskId: unknown): Promise<OperationResult<AITask>> => {
      if (!isNonBlankString(taskId)) return failure(new Error("AI 任务标识无效。"));
      return invoke(async () => {
        const result = await sidecar.request<{ task: AITask }>("ai.discardResult", { taskId });
        return result.task;
      });
    },
  );

  ipcMain.handle(
    "novalist:ai-apply-writing",
    async (event, taskId: unknown): Promise<OperationResult<DocumentSnapshot | null>> => {
      if (!isNonBlankString(taskId)) return failure(new Error("AI 任务标识无效。"));
      const owner = ownerWindow(event);
      if (owner === null) return success(null);
      const answer = await dialog.showMessageBox(owner, {
        type: "warning",
        title: "采用 AI 写作结果",
        message: "确认将审阅中的 AI 结果写入当前章节？",
        detail: "Sidecar 会再次校验项目上下文与章节版本；写入后仍可在编辑器中审阅。",
        buttons: ["取消", "确认采用"], defaultId: 0, cancelId: 0, noLink: true,
      });
      if (answer.response !== 1) return success(null);
      return invoke(async () => {
        const result = await sidecar.request<{ document: DocumentSnapshot }>("ai.applyWritingResult", { taskId });
        documentDirty = false;
        return result.document;
      });
    },
  );

  ipcMain.handle(
    "novalist:ai-commit-memory",
    async (event, taskId: unknown): Promise<OperationResult<Record<string, unknown> | null>> => {
      if (!isNonBlankString(taskId)) return failure(new Error("AI 任务标识无效。"));
      const owner = ownerWindow(event);
      if (owner === null) return success(null);
      const answer = await dialog.showMessageBox(owner, {
        type: "warning",
        title: "采用故事记忆提案",
        message: "确认写入这份记忆提案？",
        detail: "只有此确认操作会修改故事状态、章节摘要和已采用记忆记录。",
        buttons: ["取消", "确认采用"], defaultId: 0, cancelId: 0, noLink: true,
      });
      if (answer.response !== 1) return success(null);
      return invoke(async () => {
        const result = await sidecar.request<{ committed: Record<string, unknown> }>("ai.commitMemoryResult", { taskId });
        return result.committed;
      });
    },
  );

  ipcMain.handle(
    "novalist:confirm-discard",
    async (_event, actionLabel: unknown): Promise<boolean> =>
      confirmDiscard(typeof actionLabel === "string" ? actionLabel : "继续操作"),
  );
  ipcMain.on("novalist:document-dirty", (_event, dirty: unknown) => {
    if (typeof dirty === "boolean") {
      documentDirty = dirty;
    }
  });
}

async function openDocument(
  pathValue: string,
  category: string,
): Promise<OperationResult<DocumentSnapshot>> {
  return invoke(async () => {
    const result = await sidecar.request<{ document: DocumentSnapshot }>(
      "document.open",
      { path: pathValue, category },
    );
    documentDirty = false;
    return result.document;
  });
}

async function confirmDiscard(actionLabel: string): Promise<boolean> {
  const owner = mainWindow;
  if (owner === null) {
    return false;
  }
  const result = await dialog.showMessageBox(owner, {
    type: "warning",
    title: "存在未保存修改",
    message: "当前文档包含尚未保存的修改。",
    detail: `${actionLabel}将放弃这些修改。`,
    buttons: ["返回编辑", "放弃修改"],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
  });
  return result.response === 1;
}

function resolveSidecarCommand(): ConstructorParameters<typeof SidecarClient>[0] {
  const selfTestRoot = path.join(
    app.getPath("temp"),
    `novalist-sidecar-self-test-${process.pid}`,
  );
  const isolatedEnvironment = selfTestMode
    ? {
        ...process.env,
        APPDATA: path.join(selfTestRoot, "Roaming"),
        LOCALAPPDATA: path.join(selfTestRoot, "Local"),
      }
    : undefined;
  if (app.isPackaged) {
    return {
      command: path.join(process.resourcesPath, "sidecar", "NovalistSidecar.exe"),
      args: [],
      cwd: process.resourcesPath,
      ...(isolatedEnvironment === undefined ? {} : { env: isolatedEnvironment }),
    };
  }
  const configuredPython = process.env.NOVALIST_PYTHON;
  const projectPython = path.join(repositoryRoot, ".venv", "Scripts", "python.exe");
  const command = configuredPython || (existsSync(projectPython) ? projectPython : "python");
  return {
    command,
    args: ["-m", "sidecar"],
    cwd: repositoryRoot,
    ...(isolatedEnvironment === undefined ? {} : { env: isolatedEnvironment }),
  };
}

function translateEvent(event: TransportEvent): AppEvent | null {
  const data = event.data;
  if (event.event === "sidecar.ready" && typeof data.protocolVersion === "number") {
    return { event: "sidecar.ready", data: { protocolVersion: data.protocolVersion } };
  }
  if (event.event === "sidecar.stopping") {
    return { event: "sidecar.stopping", data: {} };
  }
  if (event.event === "project.closed") {
    const root = typeof data.root === "string" ? data.root : null;
    return { event: "project.closed", data: { root } };
  }
  if (event.event === "projectV2.opened" && isOpenedProjectV2(data.opened)) {
    return { event: "projectV2.opened", data: { opened: data.opened } };
  }
  if (
    event.event === "manuscript.changed" &&
    typeof data.chapterId === "string" &&
    typeof data.relativePath === "string" &&
    typeof data.revision === "string"
  ) {
    return {
      event: "manuscript.changed",
      data: {
        chapterId: data.chapterId,
        relativePath: data.relativePath,
        revision: data.revision,
        reconstructionInvalidated: data.reconstructionInvalidated === true,
      },
    };
  }
  if (
    event.event === "manuscript.structureChanged" &&
    ["created", "renamed", "reordered", "deleted", "restored", "imported"].includes(String(data.action)) &&
    typeof data.chapterId === "string"
  ) {
    return {
      event: "manuscript.structureChanged",
      data: {
        action: data.action as "created" | "renamed" | "reordered" | "deleted" | "restored" | "imported",
        chapterId: data.chapterId,
        reconstructionInvalidated: data.reconstructionInvalidated === true,
      },
    };
  }
  if (event.event === "reconstruction.updated" && typeof data.batchId === "string") {
    return { event: "reconstruction.updated", data: { batchId: data.batchId } };
  }
  if (event.event === "reconstruction.taskUpdated" && isReconstructionTask(data.task)) {
    return { event: "reconstruction.taskUpdated", data: { task: data.task } };
  }
  if (event.event === "knowledge.updated" && typeof data.kind === "string") {
    return { event: "knowledge.updated", data: { kind: data.kind } };
  }
  if (event.event === "document.changed") {
    if (
      typeof data.path === "string" &&
      typeof data.relativePath === "string" &&
      typeof data.kind === "string" &&
      typeof data.revision === "string"
    ) {
      return {
        event: "document.changed",
        data: {
          path: data.path,
          relativePath: data.relativePath,
          kind: data.kind,
          revision: data.revision,
        },
      };
    }
    return null;
  }
  if (event.event === "project.contentChanged") {
    if (typeof data.kind === "string" && isStringArray(data.changedPaths)) {
      return {
        event: "project.contentChanged",
        data: { kind: data.kind, changedPaths: data.changedPaths },
      };
    }
    return null;
  }
  if (event.event === "trash.changed") {
    if (typeof data.kind === "string" && typeof data.trashId === "string") {
      return {
        event: "trash.changed",
        data: { kind: data.kind, trashId: data.trashId },
      };
    }
    return null;
  }
  if (event.event === "ai.taskUpdated" && isAITask(data.task)) {
    return { event: "ai.taskUpdated", data: { task: data.task } };
  }
  if (
    event.event === "ai.contextReport" &&
    typeof data.taskId === "string" &&
    isRecord(data.report)
  ) {
    return { event: "ai.contextReport", data: { taskId: data.taskId, report: data.report } };
  }
  if (event.event === "project.opened" && isOpenedProjectEvent(data)) {
    return { event: "project.opened", data: { opened: data.opened } };
  }
  return null;
}

function isOpenedProjectEvent(
  data: Record<string, unknown>,
): data is { opened: OpenedProject } {
  if (!isRecord(data.opened)) {
    return false;
  }
  const project = data.opened.project;
  const migration = data.opened.migration;
  return (
    isRecord(project) &&
    typeof project.root === "string" &&
    typeof project.name === "string" &&
    typeof project.author === "string" &&
    isRecord(migration) &&
    typeof migration.fromSchema === "number" &&
    typeof migration.toSchema === "number" &&
    Array.isArray(migration.changedFiles)
  );
}

function isOpenedProject(value: unknown): value is OpenedProject {
  return isOpenedProjectEvent({ opened: value });
}

function isOpenedProjectV2(value: unknown): value is OpenedProjectV2 {
  return isRecord(value) && value.schemaVersion === 2 &&
    isNonBlankString(value.root) && isNonBlankString(value.projectId) &&
    isNonBlankString(value.name) && typeof value.author === "string";
}

function isManuscriptImportPlan(value: unknown): value is ManuscriptImportPlan {
  return isRecord(value) && isNonBlankString(value.sourceLabel) &&
    (value.sourceKind === "novalist_v1_manuscript" || value.sourceKind === "external_manuscript") &&
    isNonBlankString(value.digest) && typeof value.totalSourceBytes === "number" &&
    Array.isArray(value.chapters) && value.chapters.length > 0 && value.chapters.length <= 2_000 &&
    value.chapters.every((item) => isRecord(item) && isNonBlankString(item.chapterId) &&
      typeof item.sequence === "number" && isNonBlankString(item.title) &&
      typeof item.sourceName === "string" && typeof item.encoding === "string" &&
      typeof item.byteCount === "number" && typeof item.contentSha256 === "string" &&
      typeof item.excerpt === "string" && typeof item.empty === "boolean") &&
    Array.isArray(value.warnings) && value.warnings.every((warning) => isRecord(warning) &&
      typeof warning.code === "string" && typeof warning.message === "string" && typeof warning.source === "string");
}

function isManuscriptSnapshot(value: unknown): value is ManuscriptSnapshot {
  return isRecord(value) && Number.isInteger(value.itemCount) && Array.isArray(value.chapters) &&
    value.chapters.every((item) => isRecord(item) && isNonBlankString(item.chapterId) &&
      Number.isInteger(item.sequence) && isNonBlankString(item.title) &&
      isNonBlankString(item.path) && isNonBlankString(item.relativePath));
}

function isManuscriptTrashSnapshot(value: unknown): value is ManuscriptTrashSnapshot {
  return isRecord(value) && Array.isArray(value.items) && value.items.every((item) =>
    isRecord(item) && isNonBlankString(item.trashId) && isNonBlankString(item.chapterId) &&
    isNonBlankString(item.title) && Number.isInteger(item.sequence) && typeof item.deletedAt === "string");
}

function isManuscriptMutationResult(value: unknown): value is ManuscriptMutationResult {
  return isRecord(value) && isManuscriptSnapshot(value.snapshot) &&
    typeof value.reconstructionInvalidated === "boolean" &&
    (value.document === undefined || (isRecord(value.document) &&
      isNonBlankString(value.document.path) && isNonBlankString(value.document.relativePath) &&
      typeof value.document.category === "string" && isNonBlankString(value.document.title) &&
      typeof value.document.content === "string" && isNonBlankString(value.document.revision))) &&
    (value.trash === undefined || isManuscriptTrashSnapshot(value.trash)) &&
    (value.deletedTrashId === undefined || isNonBlankString(value.deletedTrashId));
}

function isManuscriptExportResult(value: unknown): value is ManuscriptExportResult {
  return isRecord(value) && isNonBlankString(value.path) &&
    (value.format === "md" || value.format === "txt") &&
    Number.isInteger(value.chapterCount) && Number.isInteger(value.characterCount) &&
    typeof value.sha256 === "string" && /^[0-9a-f]{64}$/u.test(value.sha256);
}

function isSaveManuscriptInput(value: unknown): value is SaveManuscriptInput {
  return isRecord(value) && isNonBlankString(value.chapterId) &&
    typeof value.content === "string" &&
    (typeof value.expectedRevision === "string" || value.expectedRevision === null) &&
    (value.force === undefined || typeof value.force === "boolean");
}

function isReconstructionSnapshot(value: unknown): value is ReconstructionSnapshot {
  return isRecord(value) && isNonBlankString(value.sourceRevision) &&
    Number.isInteger(value.acceptedEntityCount) && Number.isInteger(value.acceptedRelationCount) &&
    Number.isInteger(value.acceptedWorldCount) && Number.isInteger(value.acceptedCharacterFieldCount) && Number.isInteger(value.acceptedEventCount) &&
    Number.isInteger(value.pendingCount) && Number.isInteger(value.staleBatchCount) &&
    Array.isArray(value.batches) && value.batches.every((item) => isRecord(item) &&
      isNonBlankString(item.batchId) && isNonBlankString(item.sourceRevision) &&
      typeof item.createdAt === "string" && ["pending", "reviewed", "stale"].includes(String(item.status)) &&
      Number.isInteger(item.proposalCount) && Number.isInteger(item.pendingCount) && Number.isInteger(item.acceptedCount) &&
      ["local", "dsh"].includes(String(item.requestedMode)) && ["local", "local+dsh"].includes(String(item.producer)) &&
      typeof item.fallbackUsed === "boolean");
}

function isReconstructionBatch(value: unknown): value is ReconstructionBatch {
  return isRecord(value) && isNonBlankString(value.batchId) && isNonBlankString(value.sourceRevision) &&
    typeof value.createdAt === "string" && ["pending", "reviewed", "stale"].includes(String(value.status)) &&
    isRecord(value.chapterRevisions) && Object.values(value.chapterRevisions).every((item) => typeof item === "string") &&
    Number.isInteger(value.segmentCount) && isReconstructionExtractionSummary(value.extraction) &&
    Array.isArray(value.proposals) && value.proposals.every((item) =>
      isRecord(item) && isNonBlankString(item.proposalId) && ["entity", "relation", "world", "character_field", "event"].includes(String(item.kind)) &&
      ["pending", "accepted", "rejected"].includes(String(item.status)) && typeof item.confidence === "number" &&
      Array.isArray(item.producers) && item.producers.every((producer) => ["local", "dsh"].includes(String(producer))) &&
      ["", "single", "batch"].includes(String(item.reviewMode)) && typeof item.reviewedAt === "string" &&
      Array.isArray(item.evidence) && item.evidence.every((evidence) => isRecord(evidence) &&
        isNonBlankString(evidence.evidenceId) && isNonBlankString(evidence.chapterId) &&
        isNonBlankString(evidence.chapterRevision) && Number.isInteger(evidence.start) &&
        Number.isInteger(evidence.end) && isNonBlankString(evidence.anchor) && typeof evidence.text === "string") &&
      isReconstructionProposalContent(item));
}

function isReconstructionProposalContent(item: Record<string, unknown>): boolean {
  if (item.kind === "entity") return validKnowledgeLabel(item.name) && item.entityType === "character";
  if (item.kind === "relation") return validKnowledgeLabel(item.sourceName) && validKnowledgeLabel(item.targetName) && validKnowledgeLabel(item.label);
  if (item.kind === "world") return validKnowledgeLabel(item.name) && validKnowledgeLabel(item.category) && validKnowledgeText(item.description);
  if (item.kind === "character_field") return validKnowledgeLabel(item.characterName) && ["身份", "外貌", "性格", "目标", "能力", "阵营", "状态"].includes(String(item.field)) && validKnowledgeText(item.value);
  return validEventTime(item.timeLabel) && validKnowledgeLabel(item.title) && validKnowledgeText(item.description) &&
    isStringArray(item.characterNames) && item.characterNames.length <= 12 && item.characterNames.every(validKnowledgeLabel) &&
    isStringArray(item.worldNames) && item.worldNames.length <= 12 && item.worldNames.every(validKnowledgeLabel);
}

function isReconstructionExtractionSummary(value: unknown): boolean {
  return isRecord(value) && ["local", "dsh"].includes(String(value.requestedMode)) &&
    ["local", "local+dsh"].includes(String(value.producer)) && typeof value.fallbackUsed === "boolean" &&
    typeof value.fallbackReason === "string" && Number.isInteger(value.segmentCount) &&
    Number.isInteger(value.remoteChunkCount) && Number.isInteger(value.estimatedInputTokens) &&
    Number.isInteger(value.duplicateCount) && Number.isInteger(value.relationConflictCount) &&
    ["local_only", "manuscript_evidence_segments_only"].includes(String(value.privacyScope));
}

function isReconstructionTask(value: unknown): value is ReconstructionTask {
  return isRecord(value) && isNonBlankString(value.taskId) && typeof value.projectRoot === "string" &&
    isNonBlankString(value.sourceRevision) && ["local", "dsh"].includes(String(value.requestedMode)) &&
    ["queued", "running", "cancel_requested", "succeeded", "failed", "cancelled"].includes(String(value.status)) &&
    typeof value.stage === "string" && typeof value.progress === "number" &&
    typeof value.startedAt === "string" && (value.finishedAt === null || typeof value.finishedAt === "string") &&
    typeof value.batchId === "string" && typeof value.error === "string";
}

function isReconstructionTaskStatus(value: unknown): value is ReconstructionTaskStatus {
  return isRecord(value) && (value.active === null || isReconstructionTask(value.active)) &&
    (value.recent === null || isReconstructionTask(value.recent));
}

function validKnowledgeId(value: unknown): value is string {
  return isNonBlankString(value) && value.length <= 100;
}

function validKnowledgeLabel(value: unknown): value is string {
  return isNonBlankString(value) && value.length <= 80 && !/[\r\n\0]/u.test(value);
}

function validKnowledgeText(value: unknown): value is string {
  return isNonBlankString(value) && value.length <= 200 && !/[\r\n\0]/u.test(value);
}

function validEventTime(value: unknown): value is string {
  return isNonBlankString(value) && value.length <= 40 && !/[\r\n\0]/u.test(value);
}

function isUniqueKnowledgeIdArray(value: unknown, maximum: number): value is string[] {
  return Array.isArray(value) && value.length <= maximum && value.every(validKnowledgeId) &&
    new Set(value).size === value.length;
}

function validCharacterField(value: unknown): value is string {
  return ["身份", "外貌", "性格", "目标", "能力", "阵营", "状态"].includes(String(value));
}

function isKnowledgeWorld(value: unknown): boolean {
  return isRecord(value) && validKnowledgeId(value.worldId) && validKnowledgeLabel(value.name) &&
    validKnowledgeLabel(value.category) && validKnowledgeText(value.description) &&
    isStringArray(value.evidenceIds) && typeof value.reviewedAt === "string" && typeof value.cardRelativePath === "string";
}

function isKnowledgeEvent(value: unknown): boolean {
  return isRecord(value) && validKnowledgeId(value.eventId) && validEventTime(value.timeLabel) &&
    validKnowledgeLabel(value.title) && validKnowledgeText(value.description) &&
    isStringArray(value.participantEntityIds) && isStringArray(value.worldIds) &&
    isStringArray(value.evidenceIds) && typeof value.reviewedAt === "string" && Number.isInteger(value.order);
}

function isKnowledgeEvidence(value: unknown): boolean {
  return isRecord(value) && validKnowledgeId(value.evidenceId) && validKnowledgeId(value.chapterId) &&
    isNonBlankString(value.chapterRevision) && typeof value.start === "number" && Number.isInteger(value.start) &&
    typeof value.end === "number" && Number.isInteger(value.end) && value.start >= 0 && value.end >= value.start &&
    isNonBlankString(value.anchor) &&
    typeof value.text === "string" && value.text.length <= 4000;
}

function isKnowledgeDiagnostic(value: unknown): boolean {
  return isRecord(value) && validKnowledgeId(value.diagnosticId) &&
    ["temporal_overlap", "missing_character_link", "inactive_world_link"].includes(String(value.code)) &&
    ["notice", "warning"].includes(String(value.severity)) &&
    isNonBlankString(value.message) && value.message.length <= 500 &&
    isUniqueKnowledgeIdArray(value.eventIds, 1000) && isUniqueKnowledgeIdArray(value.evidenceIds, 1000);
}

function isKnowledgeCard(value: unknown): value is KnowledgeCardDocument {
  return isRecord(value) && ["character", "world"].includes(String(value.ownerKind)) &&
    validKnowledgeId(value.ownerId) && ["generated", "author"].includes(String(value.mode)) &&
    validKnowledgeLabel(value.title) && typeof value.relativePath === "string" && value.relativePath.length <= 200 &&
    typeof value.content === "string" && value.content.length <= 200_000 &&
    isNonBlankString(value.revision) && value.revision.length <= 100 && typeof value.readOnly === "boolean" &&
    value.readOnly === (value.mode === "generated");
}

function isKnowledgeSnapshot(value: unknown): value is KnowledgeSnapshot {
  if (!isRecord(value) || !Array.isArray(value.entities) || !Array.isArray(value.relations) ||
      !Array.isArray(value.worlds) || !Array.isArray(value.hiddenWorlds) || !Array.isArray(value.events) ||
      !Array.isArray(value.hiddenEvents) || !Array.isArray(value.evidence) || !Array.isArray(value.diagnostics) ||
      !Array.isArray(value.merges) || !Number.isInteger(value.operationCount)) return false;
  const entityIds = new Set<string>();
  const entitiesValid = value.entities.every((item) => {
    if (!isRecord(item) || !validKnowledgeId(item.entityId) || item.entityType !== "character" ||
        !validKnowledgeLabel(item.displayName) || !isStringArray(item.aliases) || item.aliases.length > 20 ||
        !item.aliases.every((alias) => alias.length <= 80) || !isRecord(item.profileFields) ||
        !Object.entries(item.profileFields).every(([field, fieldValue]) => validCharacterField(field) && validKnowledgeText(fieldValue)) ||
        !isRecord(item.hiddenProfileFields) || !Object.entries(item.hiddenProfileFields).every(([field, fieldValue]) => validCharacterField(field) && validKnowledgeText(fieldValue)) ||
        !isRecord(item.profileEvidenceIds) || !Object.values(item.profileEvidenceIds).every((ids) => isStringArray(ids)) ||
        !isStringArray(item.evidenceIds) || typeof item.reviewedAt !== "string" || typeof item.cardRelativePath !== "string") return false;
    entityIds.add(item.entityId);
    return true;
  });
  const worldIds = new Set(value.worlds.filter(isKnowledgeWorld).map((item) => String((item as Record<string, unknown>).worldId)));
  const eventIds = new Set(value.events.filter(isKnowledgeEvent).map((item) => String((item as Record<string, unknown>).eventId)));
  const evidenceIds = new Set(value.evidence.filter(isKnowledgeEvidence).map((item) => String((item as Record<string, unknown>).evidenceId)));
  return entitiesValid && value.relations.every((item) => isRecord(item) && validKnowledgeId(item.relationId) &&
    validKnowledgeId(item.sourceEntityId) && validKnowledgeId(item.targetEntityId) &&
    entityIds.has(item.sourceEntityId) && entityIds.has(item.targetEntityId) && validKnowledgeLabel(item.label) &&
    isStringArray(item.evidenceIds) && typeof item.reviewedAt === "string") &&
    value.worlds.every(isKnowledgeWorld) && value.hiddenWorlds.every(isKnowledgeWorld) &&
    value.events.every((item) => isKnowledgeEvent(item) && isRecord(item) &&
      (item.participantEntityIds as string[]).every((id) => entityIds.has(id)) &&
      (item.worldIds as string[]).every((id) => worldIds.has(id))) && value.hiddenEvents.every(isKnowledgeEvent) &&
    value.evidence.every(isKnowledgeEvidence) && value.diagnostics.every((item) => isKnowledgeDiagnostic(item) && isRecord(item) &&
      (item.eventIds as string[]).every((id) => eventIds.has(id)) &&
      (item.evidenceIds as string[]).every((id) => evidenceIds.has(id))) &&
    value.merges.every((item) => isRecord(item) && validKnowledgeId(item.sourceEntityId) &&
      validKnowledgeId(item.targetEntityId) && typeof item.sourceName === "string" && typeof item.targetName === "string");
}

async function invokeKnowledge(method: string, params: Record<string, unknown>): Promise<OperationResult<KnowledgeSnapshot>> {
  return invoke(async () => {
    const response = await sidecar.request<{ knowledge: KnowledgeSnapshot }>(method, params);
    if (!isKnowledgeSnapshot(response.knowledge)) throw new Error("Sidecar 返回了无效的知识整理状态。");
    return response.knowledge;
  });
}

function isPreferencesSnapshot(value: unknown): value is PreferencesSnapshot {
  return isRecord(value) &&
    (value.theme === "light" || value.theme === "dark") &&
    isIntegerInRange(value.uiFontSize, 10, 22) &&
    isIntegerInRange(value.editorFontSize, 12, 36) &&
    typeof value.autoSave === "boolean" &&
    isIntegerInRange(value.autoSaveInterval, 5, 600) &&
    typeof value.showLineNumbers === "boolean" &&
    typeof value.lastProject === "string" &&
    isStringArray(value.recentProjects);
}

function isIntegerInRange(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum && value <= maximum;
}

function isPreferencesPatch(value: unknown): value is PreferencesPatch {
  if (!isRecord(value)) return false;
  const allowed = new Set([
    "theme", "uiFontSize", "editorFontSize", "autoSave",
    "autoSaveInterval", "showLineNumbers",
  ]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return false;
  return (value.theme === undefined || value.theme === "light" || value.theme === "dark") &&
    (value.uiFontSize === undefined || Number.isInteger(value.uiFontSize)) &&
    (value.editorFontSize === undefined || Number.isInteger(value.editorFontSize)) &&
    (value.autoSave === undefined || typeof value.autoSave === "boolean") &&
    (value.autoSaveInterval === undefined || Number.isInteger(value.autoSaveInterval)) &&
    (value.showLineNumbers === undefined || typeof value.showLineNumbers === "boolean");
}

function preferencesPatchDto(patch: PreferencesPatch): Record<string, unknown> {
  return {
    ...(patch.theme === undefined ? {} : { theme: patch.theme }),
    ...(patch.uiFontSize === undefined ? {} : { ui_font_size: patch.uiFontSize }),
    ...(patch.editorFontSize === undefined ? {} : { editor_font_size: patch.editorFontSize }),
    ...(patch.autoSave === undefined ? {} : { auto_save: patch.autoSave }),
    ...(patch.autoSaveInterval === undefined ? {} : { auto_save_interval: patch.autoSaveInterval }),
    ...(patch.showLineNumbers === undefined ? {} : { show_line_numbers: patch.showLineNumbers }),
  };
}

function isSaveDocumentInput(value: unknown): value is SaveDocumentInput {
  return (
    isRecord(value) &&
    typeof value.path === "string" &&
    typeof value.category === "string" &&
    typeof value.content === "string" &&
    (typeof value.expectedRevision === "string" || value.expectedRevision === null) &&
    (value.force === undefined || typeof value.force === "boolean")
  );
}

function isNonBlankString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isCanonKind(value: unknown): value is "character" | "world" | "power" {
  return value === "character" || value === "world" || value === "power";
}

function isDeletableKind(value: unknown): value is ProjectDocumentItem["kind"] {
  return value === "chapter" || value === "character" || value === "world" || value === "power" || value === "timeline";
}

function isTrashKind(value: unknown): value is TrashItem["kind"] {
  return value === "chapter" || value === "character" || value === "world" || value === "power" || value === "timeline";
}

function isConflictPolicy(value: unknown): value is "error" | "rename" {
  return value === "error" || value === "rename";
}

function isProjectDocumentItem(value: unknown): value is ProjectDocumentItem {
  return (
    isRecord(value) &&
    isNonBlankString(value.id) &&
    isNonBlankString(value.title) &&
    isNonBlankString(value.path) &&
    typeof value.protected === "boolean" &&
    typeof value.kind === "string"
  );
}

function isTrashItem(value: unknown): value is TrashItem {
  return (
    isRecord(value) &&
    isTrashKind(value.kind) &&
    isNonBlankString(value.trashId) &&
    isNonBlankString(value.title)
  );
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isRelationshipGraphSnapshot(
  value: unknown,
): value is RelationshipGraphSnapshot {
  if (
    !isRecord(value) ||
    typeof value.projectName !== "string" ||
    !isNonBlankString(value.revision) ||
    !Array.isArray(value.nodes) ||
    !Array.isArray(value.edges) ||
    !Array.isArray(value.warnings) ||
    !isStringArray(value.relationTypes)
  ) return false;
  if (!value.nodes.every(isGraphNode) || !value.edges.every(isGraphEdge)) return false;
  if (!value.warnings.every((warning) => isRecord(warning) &&
    typeof warning.code === "string" && typeof warning.message === "string" &&
    typeof warning.subject === "string" && typeof warning.target === "string")) return false;
  const nodeIds = new Set(value.nodes.map((node) => node.id));
  return nodeIds.size === value.nodes.length && value.edges.every(
    (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target),
  );
}

function isGraphNode(value: unknown): value is RelationshipGraphSnapshot["nodes"][number] {
  return isRecord(value) && isNonBlankString(value.id) && isNonBlankString(value.name) &&
    isStringArray(value.aliases) && typeof value.state === "string" &&
    typeof value.location === "string" && typeof value.resolved === "boolean" &&
    (value.path === null || typeof value.path === "string") &&
    (value.relativePath === null || typeof value.relativePath === "string") &&
    ["character", "world", "event"].includes(String(value.nodeKind)) &&
    (value.order === null || Number.isInteger(value.order));
}

function isGraphEdge(value: unknown): value is RelationshipGraphSnapshot["edges"][number] {
  return isRecord(value) && isNonBlankString(value.id) && isNonBlankString(value.source) &&
    isNonBlankString(value.target) && isNonBlankString(value.label) && value.directed === true &&
    ["story_state", "character_card", "reviewed_v2"].includes(String(value.sourceKind)) &&
    ["relationship", "participation", "setting"].includes(String(value.edgeKind)) &&
    Array.isArray(value.evidence) && value.evidence.every((item) =>
      isRecord(item) && typeof item.chapterId === "string" && typeof item.anchor === "string" &&
      typeof item.description === "string" && typeof item.certainty === "string");
}

function isAIStartInput(value: unknown): value is AIStartInput {
  if (!isRecord(value) || !isAITaskKind(value.kind)) return false;
  if (typeof value.chapterId !== "string" || (typeof value.sourceRevision !== "string" && value.sourceRevision !== null)) return false;
  if (typeof value.noticeAccepted !== "boolean") return false;
  if (value.options === undefined) return true;
  if (!isRecord(value.options)) return false;
  const unknown = Object.keys(value.options).filter((key) => key !== "selectedPower" && key !== "selectedForeshadowing");
  return unknown.length === 0 &&
    (value.options.selectedPower === undefined || isStringArray(value.options.selectedPower)) &&
    (value.options.selectedForeshadowing === undefined ||
      (Array.isArray(value.options.selectedForeshadowing) && value.options.selectedForeshadowing.every(isRecord)));
}

function isAITaskKind(value: unknown): value is AITask["kind"] {
  return value === "expand" || value === "continuation" || value === "check" || value === "memory" || value === "connection";
}

function isAITask(value: unknown): value is AITask {
  return isRecord(value) && isNonBlankString(value.taskId) && isAITaskKind(value.kind) &&
    typeof value.chapterId === "string" && isAITaskState(value.status) &&
    typeof value.stage === "string" && typeof value.progress === "number" &&
    typeof value.startedAt === "string" && typeof value.error === "string" &&
    typeof value.hasResult === "boolean";
}

function isAITaskState(value: unknown): value is AITask["status"] {
  return value === "queued" || value === "running" || value === "cancel_requested" ||
    value === "succeeded" || value === "failed" || value === "cancelled" ||
    value === "applied" || value === "discarded";
}

function isAIResultEnvelope(value: unknown): value is AIResultEnvelope {
  if (!isRecord(value) || !isAITask(value.task) || !isRecord(value.result)) return false;
  const result = value.result;
  if (result.type === "writing") {
    return (result.mode === "replace" || result.mode === "append") &&
      typeof result.text === "string" && typeof result.charCount === "number";
  }
  if (result.type === "consistency") return isRecord(result.report) && typeof result.formatted === "string";
  if (result.type === "memory") return typeof result.summary === "string" &&
    typeof result.preview === "string" && typeof result.hasBlockers === "boolean";
  return result.type === "connection" && typeof result.message === "string";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function ownerWindow(event: IpcMainInvokeEvent): BrowserWindow | null {
  return BrowserWindow.fromWebContents(event.sender) ?? mainWindow;
}

async function invoke<T>(operation: () => Promise<T>): Promise<OperationResult<T>> {
  try {
    return success(await operation());
  } catch (error) {
    return failure(error);
  }
}

function success<T>(value: T): OperationResult<T> {
  return { ok: true, value };
}

function failure<T>(error: unknown): OperationResult<T> {
  return { ok: false, error: publicError(error) };
}

function publicError(error: unknown): RpcError {
  if (error instanceof SidecarRpcError) {
    return {
      code: error.code,
      message: error.message,
      retryable: error.retryable,
      ...(error.data === undefined ? {} : { data: error.data }),
    };
  }
  return {
    code: "DESKTOP_ERROR",
    message: error instanceof Error ? error.message : "桌面端操作失败。",
    retryable: false,
  };
}

async function shutdownAndQuit(): Promise<void> {
  if (shutdownStarted) {
    return;
  }
  shutdownStarted = true;
  await sidecar.stop();
  app.quit();
}

async function runSelfTest(): Promise<void> {
  try {
    const ping = await sidecar.request<{ alive: boolean }>("system.ping");
    if (!ping.alive || handshake?.protocolVersion !== 1 || mainWindow === null) {
      throw new Error("自检握手或窗口状态无效。");
    }
    const preloadAvailable = await mainWindow.webContents.executeJavaScript(
      "typeof window.novalist === 'object' && typeof window.novalist.chooseAndScanManuscript === 'function' && typeof window.novalist.createProjectV2 === 'function' && typeof window.novalist.openManuscript === 'function' && typeof window.novalist.saveManuscript === 'function' && typeof window.novalist.generateReconstruction === 'function' && typeof window.novalist.reviewReconstruction === 'function' && typeof window.novalist.reviewReconstructionMany === 'function' && typeof window.novalist.getKnowledgeSnapshot === 'function' && typeof window.novalist.unmergeKnowledgeEntity === 'function' && typeof window.novalist.updateKnowledgeCharacterField === 'function' && typeof window.novalist.hideKnowledgeWorld === 'function' && typeof window.novalist.updateKnowledgeEvent === 'function' && typeof window.novalist.updateKnowledgeEventLinks === 'function' && typeof window.novalist.reorderKnowledgeEvents === 'function' && typeof window.novalist.restoreKnowledgeEvent === 'function' && typeof window.novalist.openKnowledgeCard === 'function' && typeof window.novalist.saveKnowledgeAuthorCard === 'function'",
      true,
    );
    if (preloadAvailable !== true) {
      throw new Error("受限 preload API 未加载。");
    }
    if (expectedReleaseKeyId !== undefined &&
        (releaseTrust?.mode !== "production" || releaseTrust.keyId !== expectedReleaseKeyId)) {
      throw new Error("打包应用内嵌的发布公钥指纹与验收值不一致。");
    }
    if (performanceReportPath !== undefined) {
      if (performanceReportPath.trim().length === 0) {
        throw new Error("性能报告路径不能为空。");
      }
      await runRendererPerformanceProbe(mainWindow, performanceReportPath);
      console.log("electron-renderer-performance-probe: ok");
      allowWindowClose = true;
      mainWindow.close();
      return;
    }
    if (selfTestProjectPath !== undefined) {
      if (selfTestProjectPath.trim().length === 0) {
        throw new Error("打包自检项目路径不能为空。");
      }
      const response = await sidecar.request<{ opened: OpenedProjectV2 }>(
        "project.openV2",
        { path: selfTestProjectPath },
      );
      if (!isOpenedProjectV2(response.opened) || response.opened.schemaVersion !== 2) {
        throw new Error("打包自检无法打开 schema-v2 项目。");
      }
      const manuscript = await sidecar.request<{ snapshot: ManuscriptSnapshot }>(
        "manuscript.snapshot",
      );
      if (!isManuscriptSnapshot(manuscript.snapshot) || manuscript.snapshot.itemCount < 1) {
        throw new Error("打包自检项目没有可读取的正文。");
      }
      await verifyV2WorkflowSurface(mainWindow);
    }
    if (capturePreview) {
      mainWindow.showInactive();
      await new Promise((resolve) => setTimeout(resolve, 350));
      const legacySource = path.join(
        repositoryRoot,
        "tests",
        "fixtures",
        "electron_migration",
        "golden_project",
      );
      const scanned = await sidecar.request<{ plan: ManuscriptImportPlan }>(
        "manuscript.scanImport",
        { sourcePath: legacySource },
      );
      await sidecar.request<{ opened: OpenedProjectV2 }>("project.createV2", {
        parentDirectory: path.join(electronRoot, "dist"),
        name: `electron-v2-preview-${process.pid}`,
        author: "DeepSonder self-test",
        planDigest: scanned.plan.digest,
      });
      const projectRendered = await waitForRendererCondition(
        mainWindow,
        `document.querySelector(".project-heading strong")?.textContent === "electron-v2-preview-${process.pid}" && document.querySelector(".codemirror-editor .cm-editor") !== null && document.querySelector(".panel-footer")?.textContent?.includes("Schema v2")`,
      );
      if (!projectRendered) throw new Error("预览项目或首篇文档未完成渲染。");
      await verifyV2WorkflowSurface(mainWindow);
      const seededChapter = await sidecar.request<{ document: DocumentSnapshot }>("manuscript.open", {
        chapterId: "chapter_0001",
      });
      await sidecar.request("manuscript.save", {
        chapterId: "chapter_0001",
        content: `${seededChapter.document.content}\n\n[[角色字段:林砚|身份|雾港调查员]] [[世界:雾港|地点|终年被浓雾笼罩的港城]] [[事件:雨夜|收到密信|林砚与苏乔在雾港收到密信|林砚、苏乔|雾港]] [[事件:雨夜|前往灯塔|林砚决定前往旧灯塔|林砚|雾港]]\n`,
        expectedRevision: seededChapter.document.revision,
      });
      const previewButtonOpened = await mainWindow.webContents.executeJavaScript(
        `(() => {
          const button = Array.from(document.querySelectorAll(".view-toggle button"))
            .find((item) => item.textContent === "预览");
          if (!(button instanceof HTMLButtonElement)) return false;
          button.click();
          return true;
        })()`,
        true,
      );
      if (previewButtonOpened !== true) throw new Error("Markdown 预览入口未渲染。");
      const previewRendered = await waitForRendererCondition(
        mainWindow,
        `document.querySelector(".markdown-preview") !== null`,
      );
      if (!previewRendered) throw new Error("Markdown 预览未完成渲染。");
      const reconstructionOpened = await mainWindow.webContents.executeJavaScript(
        `(() => {
          const button = Array.from(document.querySelectorAll(".rail-item"))
            .find((item) => item.textContent?.includes("识别"));
          if (!(button instanceof HTMLButtonElement)) return false;
          button.click();
          return true;
        })()`,
        true,
      );
      if (reconstructionOpened !== true) throw new Error("正文识别入口未渲染。");
      const reconstructionModeRendered = await waitForRendererCondition(
        mainWindow,
        `document.querySelector(".reconstruction-mode input[type=checkbox]") !== null`,
      );
      if (!reconstructionModeRendered) throw new Error("DSH 增强识别授权控件未渲染。");
      const reconstructionStarted = await mainWindow.webContents.executeJavaScript(
        `(() => {
          const button = document.querySelector(".reconstruction-actions .primary-button");
          if (!(button instanceof HTMLButtonElement)) return false;
          button.click();
          return true;
        })()`,
        true,
      );
      if (reconstructionStarted !== true) throw new Error("后台正文识别任务无法启动。");
      const proposalsRendered = await waitForRendererCondition(
        mainWindow,
        `document.querySelectorAll(".proposal-card").length >= 2 && document.querySelector(".review-filters") !== null && document.querySelector(".bulk-review") !== null`,
      );
      if (!proposalsRendered) throw new Error("证据审核工作台未完成渲染。");
      const proposalsSelected = await mainWindow.webContents.executeJavaScript(
        `(() => {
          window.confirm = () => true;
          const checkbox = document.querySelector(".bulk-review input[type=checkbox]");
          if (!(checkbox instanceof HTMLInputElement)) return false;
          checkbox.click();
          return true;
        })()`,
        true,
      );
      if (proposalsSelected !== true) throw new Error("批量候选选择不可用。");
      const batchActionReady = await waitForRendererCondition(
        mainWindow,
        `document.querySelector(".bulk-review .accept") instanceof HTMLButtonElement && document.querySelector(".bulk-review .accept").disabled === false`,
      );
      if (!batchActionReady) throw new Error("批量候选接受操作不可用。");
      await mainWindow.webContents.executeJavaScript(
        `document.querySelector(".bulk-review .accept").click()`,
        true,
      );
      const reviewRendered = await waitForRendererCondition(
        mainWindow,
        `document.querySelector(".reconstruction-stats")?.textContent?.includes("2人物") === true && document.querySelector(".reconstruction-stats")?.textContent?.includes("1世界观") === true && document.querySelector(".reconstruction-stats")?.textContent?.includes("1角色字段") === true && document.querySelector(".reconstruction-stats")?.textContent?.includes("2事件") === true && document.querySelectorAll(".proposal-card").length === 0`,
      );
      if (!reviewRendered) throw new Error("批量候选审核结果未完成渲染。");
      const graphOpened = await mainWindow.webContents.executeJavaScript(
        `(() => {
          const button = Array.from(document.querySelectorAll(".rail-item"))
            .find((item) => item.textContent?.includes("图谱"));
          if (!(button instanceof HTMLButtonElement)) return false;
          button.click();
          return true;
        })()`,
        true,
      );
      if (graphOpened !== true) throw new Error("v2 图谱入口不可用。");
      const graphRendered = await waitForRendererCondition(
        mainWindow,
        `document.querySelectorAll(".graph-index nav button").length === 5 && document.querySelectorAll(".event-sequence button").length === 2`,
      );
      if (!graphRendered) throw new Error("已审核 v2 人物未投影到图谱。");
      const knowledgeOpened = await mainWindow.webContents.executeJavaScript(
        `(() => {
          const button = Array.from(document.querySelectorAll(".rail-item"))
            .find((item) => item.textContent?.includes("知识"));
          if (!(button instanceof HTMLButtonElement)) return false;
          button.click();
          return true;
        })()`,
        true,
      );
      if (knowledgeOpened !== true) throw new Error("知识整理入口不可用。");
      const knowledgeRendered = await waitForRendererCondition(
        mainWindow,
        `document.querySelector(".knowledge-drawer") !== null && document.querySelectorAll(".knowledge-card.entity-card").length === 2 && document.querySelectorAll(".knowledge-card.world-card").length === 1 && document.querySelectorAll(".knowledge-card.event-card").length === 2 && document.querySelectorAll(".event-link-editor").length === 4 && document.querySelector(".knowledge-diagnostic") !== null && document.querySelectorAll(".profile-field-editor input").length === 1 && document.querySelectorAll(".open-author-card").length === 3`,
      );
      if (!knowledgeRendered) throw new Error("已审核人物未进入知识整理界面。");
      const eventCurationRoundTrip = await mainWindow.webContents.executeJavaScript(
        `(async () => {
          const snapshot = await window.novalist.getKnowledgeSnapshot();
          if (!snapshot.ok || snapshot.value.events.length !== 2) return false;
          const first = snapshot.value.events[0];
          const entityId = snapshot.value.entities[0]?.entityId;
          if (!entityId) return false;
          const linked = await window.novalist.updateKnowledgeEventLinks(first.eventId, [entityId], []);
          if (!linked.ok) return false;
          const ids = linked.value.events.map((item) => item.eventId).reverse();
          const reordered = await window.novalist.reorderKnowledgeEvents(ids);
          return reordered.ok && reordered.value.events[0]?.eventId === ids[0] && reordered.value.diagnostics.some((item) => item.code === "temporal_overlap");
        })()`,
        true,
      );
      if (eventCurationRoundTrip !== true) throw new Error("事件连接或人工排序无法通过受限桥接保存。");
      const cardRoundTrip = await mainWindow.webContents.executeJavaScript(
        `(async () => {
          const snapshot = await window.novalist.getKnowledgeSnapshot();
          if (!snapshot.ok || snapshot.value.entities.length === 0) return false;
          const entityId = snapshot.value.entities[0].entityId;
          const opened = await window.novalist.openKnowledgeCard("character", entityId, "author");
          if (!opened.ok || opened.value.readOnly) return false;
          const saved = await window.novalist.saveKnowledgeAuthorCard(
            "character", entityId, opened.value.content + "\\n阶段十五自检。\\n", opened.value.revision,
          );
          return saved.ok && saved.value.content.includes("阶段十五自检") && saved.value.revision !== opened.value.revision;
        })()`,
        true,
      );
      if (cardRoundTrip !== true) throw new Error("作者知识卡无法通过受限桥接读写。");
      const cardOpened = await mainWindow.webContents.executeJavaScript(
        `(() => {
          const button = document.querySelector(".entity-card .open-author-card");
          if (!(button instanceof HTMLButtonElement)) return false;
          button.click();
          return true;
        })()`,
        true,
      );
      if (cardOpened !== true || !await waitForRendererCondition(
        mainWindow,
        `document.querySelector(".knowledge-card-editor textarea:not([readonly])") !== null && document.querySelector(".knowledge-card-editor")?.textContent?.includes("作者内容不会被正文重建覆盖") === true`,
      )) throw new Error("作者知识卡编辑器未完成渲染。");
      await mainWindow.webContents.executeJavaScript(
        `document.querySelector(".knowledge-card-editor .icon-button")?.click()`,
        true,
      );
      mainWindow.webContents.invalidate();
      await new Promise((resolve) => setTimeout(resolve, 250));
      const image = await mainWindow.webContents.capturePage();
      const outputPath = path.join(electronRoot, "dist", "electron-preview.png");
      await writeFile(outputPath, image.toPNG());
      console.log(`electron-preview-capture: ${outputPath}`);
    }
    console.log("electron-preview-self-test: ok");
    allowWindowClose = true;
    mainWindow.close();
  } catch (error) {
    console.error(`electron-preview-self-test: ${publicError(error).message}`);
    await sidecar.stop();
    app.exit(1);
  }
}

async function runRendererPerformanceProbe(
  window: BrowserWindow,
  outputPath: string,
): Promise<void> {
  const sourcePath = path.join(
    repositoryRoot,
    "tests",
    "fixtures",
    "electron_migration",
    "golden_project",
  );
  const scanned = await sidecar.request<{ plan: ManuscriptImportPlan }>(
    "manuscript.scanImport",
    { sourcePath },
  );
  const projectName = `phase-23b-renderer-${process.pid}`;
  const created = await sidecar.request<{ opened: OpenedProjectV2 }>("project.createV2", {
    parentDirectory: app.getPath("temp"),
    name: projectName,
    author: "DeepSonder renderer performance probe",
    planDigest: scanned.plan.digest,
  });
  try {
    const initialRendered = await waitForRendererCondition(
      window,
      `document.querySelector(".project-heading strong")?.textContent === ${JSON.stringify(projectName)} && document.querySelector(".codemirror-editor .cm-editor") !== null`,
      10_000,
    );
    if (!initialRendered) throw new Error("性能探针项目未完成初始渲染。");
    const initialMemory = rendererMemoryInfo(window);
    const initial = await sidecar.request<{ document: DocumentSnapshot }>("manuscript.open", {
      chapterId: "chapter_0001",
    });
    const content = createPerformanceManuscript(1_000_000);
    const saved = await sidecar.request<{ document: DocumentSnapshot }>("manuscript.save", {
      chapterId: "chapter_0001",
      content,
      expectedRevision: initial.document.revision,
    });
    const switchedAway = await clickRendererChapter(window, "chapter_0002");
    if (!switchedAway || !await waitForRendererCondition(
      window,
      `document.querySelector(".document-row.active small")?.textContent === "chapter_0002"`,
      10_000,
    )) throw new Error("性能探针无法切换到对照章节。");
    const startedAt = performance.now();
    const switchedBack = await clickRendererChapter(window, "chapter_0001");
    if (!switchedBack || !await waitForRendererCondition(
      window,
      `document.querySelector(".document-row.active small")?.textContent === "chapter_0001" && document.querySelector(".codemirror-editor")?.getAttribute("data-document-length") === ${JSON.stringify(String(saved.document.content.length))} && document.querySelector(".codemirror-editor .cm-editor") !== null`,
      15_000,
    )) throw new Error("百万字符章节未在性能预算窗口内完成渲染。");
    const editorReadyMs = Number((performance.now() - startedAt).toFixed(2));
    const finalMemory = rendererMemoryInfo(window);
    const editorReadyBudgetMs = 3_000;
    const rendererPrivateDeltaBudgetKb = 256 * 1024;
    const privateDeltaKb = initialMemory.privateBytes === undefined || finalMemory.privateBytes === undefined
      ? null
      : Math.max(0, finalMemory.privateBytes - initialMemory.privateBytes);
    if (editorReadyMs > editorReadyBudgetMs) {
      throw new Error(`百万字符编辑器就绪耗时超出预算：${editorReadyMs} ms。`);
    }
    if (privateDeltaKb !== null && privateDeltaKb > rendererPrivateDeltaBudgetKb) {
      throw new Error(`百万字符 renderer 私有内存增量超出预算：${privateDeltaKb} KiB。`);
    }
    const report = {
      schemaVersion: 1,
      result: "passed",
      generatedAtUtc: new Date().toISOString(),
      applicationVersion: app.getVersion(),
      manuscriptCharacters: saved.document.content.length,
      editorReadyMs,
      budgets: {
        editorReadyMs: editorReadyBudgetMs,
        rendererPrivateDeltaKb: rendererPrivateDeltaBudgetKb,
      },
      rendererMemory: {
        initialPrivateKb: initialMemory.privateBytes ?? null,
        finalPrivateKb: finalMemory.privateBytes ?? null,
        privateDeltaKb,
        initialWorkingSetKb: initialMemory.workingSetSize,
        finalWorkingSetKb: finalMemory.workingSetSize,
        workingSetDeltaKb: Math.max(0, finalMemory.workingSetSize - initialMemory.workingSetSize),
      },
    };
    const resolvedOutput = path.resolve(electronRoot, outputPath);
    await writeFile(resolvedOutput, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  } finally {
    await rm(created.opened.root, { recursive: true, force: true });
  }
}

function rendererMemoryInfo(window: BrowserWindow): Electron.MemoryInfo {
  const rendererPid = window.webContents.getOSProcessId();
  const metric = app.getAppMetrics().find((item) => item.pid === rendererPid);
  if (metric === undefined) {
    throw new Error("无法读取 Electron renderer 进程内存指标。");
  }
  return metric.memory;
}

function createPerformanceManuscript(targetCharacters: number): string {
  const paragraph = "林砚沿着雾港潮湿的石阶前行，记录潮声、灯塔与人物关系的细微变化。".repeat(6);
  const sections: string[] = [];
  let length = 0;
  for (let index = 1; length < targetCharacters; index += 1) {
    const section = `## 场景 ${index}\n\n${paragraph}\n\n`;
    sections.push(section);
    length += section.length;
  }
  return sections.join("").slice(0, targetCharacters);
}

async function clickRendererChapter(window: BrowserWindow, chapterId: string): Promise<boolean> {
  return window.webContents.executeJavaScript(
    `(() => {
      const row = Array.from(document.querySelectorAll(".document-row"))
        .find((item) => item.querySelector("small")?.textContent === ${JSON.stringify(chapterId)});
      const button = row?.querySelector(".document-item");
      if (!(button instanceof HTMLButtonElement)) return false;
      button.click();
      return true;
    })()`,
    true,
  ) as Promise<boolean>;
}

async function verifyV2WorkflowSurface(window: BrowserWindow): Promise<void> {
  const rendered = await waitForRendererCondition(
    window,
    `document.querySelector(".panel-footer")?.textContent?.includes("Schema v2") === true && document.querySelector(".document-row") !== null`,
  );
  if (!rendered) throw new Error("schema-v2 工作区未完成渲染。");
  const controlsReady = await window.webContents.executeJavaScript(
    `(() => {
      const quickLabels = Array.from(document.querySelectorAll(".quick-actions button"))
        .map((item) => item.textContent?.trim());
      const rowTitles = Array.from(document.querySelectorAll(".chapter-row-actions button"))
        .map((item) => item.getAttribute("title"));
      const create = document.querySelector(".panel-heading .icon-button");
      return create instanceof HTMLButtonElement && !create.disabled &&
        ["追加导入", "导出 MD", "导出 TXT"].every((label) => quickLabels.includes(label)) &&
        ["下移", "重命名", "移入正文回收站"].every((title) => rowTitles.includes(title));
    })()`,
    true,
  );
  if (controlsReady !== true) {
    throw new Error("schema-v2 章节、追加导入或导出入口不完整。");
  }
  const aiOpened = await window.webContents.executeJavaScript(
    `(() => {
      const button = Array.from(document.querySelectorAll(".rail-item"))
        .find((item) => item.textContent?.includes("AI"));
      if (!(button instanceof HTMLButtonElement) || button.disabled) return false;
      button.click();
      return true;
    })()`,
    true,
  );
  const aiReady = aiOpened === true && await waitForRendererCondition(
    window,
    `document.querySelector(".ai-scope")?.textContent?.includes("已审核的 v2 知识") === true && Array.from(document.querySelectorAll(".ai-actions button")).filter((item) => !item.disabled).length === 5`,
  );
  if (!aiReady) throw new Error("schema-v2 AI 入口或上下文告知未完成渲染。");
  await window.webContents.executeJavaScript(
    `document.querySelector(".ai-drawer .icon-button")?.click()`,
    true,
  );
}

async function waitForRendererCondition(
  window: BrowserWindow,
  expression: string,
  timeoutMs = 5_000,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const matched = await window.webContents.executeJavaScript(
      `Boolean(${expression})`,
      true,
    );
    if (matched === true) return true;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

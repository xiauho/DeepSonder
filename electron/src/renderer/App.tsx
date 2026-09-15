import { FormEvent, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AIResult,
  AITask,
  AITaskKind,
  DocumentKind, DocumentSnapshot, GraphNode, OpenedProject, ProjectDocumentItem,
  ManuscriptItem, ManuscriptMutationResult, ManuscriptSnapshot, ManuscriptTrashItem, ManuscriptTrashSnapshot,
  OpenedProjectV2, PreferencesSnapshot, ProjectSnapshot,
  RpcError, SaveDocumentInput, SaveManuscriptInput, TrashItem, TrashSnapshot,
} from "../shared/contracts";
import { MarkdownPreview } from "./features/editor/MarkdownPreview";
import { ImportWizard } from "./features/importing/ImportWizard";
import { ReconstructionDrawer } from "./features/reconstruction/ReconstructionDrawer";
import { KnowledgeDrawer } from "./features/knowledge/KnowledgeDrawer";
import brandIconUrl from "./assets/app_icon.png";

const GraphView = lazy(() => import("./features/graph/GraphView").then(
  (module) => ({ default: module.GraphView }),
));
const MarkdownEditor = lazy(() => import("./features/editor/MarkdownEditor").then(
  (module) => ({ default: module.MarkdownEditor }),
));

type View = "write" | "graph";
type CreateKind = "chapter" | "character" | "world" | "power";

const KIND_LABEL: Record<DocumentKind, string> = {
  chapter: "章节", outline: "大纲", plan: "规划", style: "写作",
  character: "角色", world: "世界", power: "体系", timeline: "时间线",
};
const GROUPS: Array<{ key: keyof ProjectSnapshot; label: string }> = [
  { key: "chapters", label: "章节" }, { key: "outlines", label: "结构与写作" },
  { key: "characters", label: "角色" }, { key: "world", label: "世界观" },
  { key: "power", label: "体系" }, { key: "timeline", label: "时间线" },
];
const DEFAULT_PREFERENCES: PreferencesSnapshot = {
  theme: "light", uiFontSize: 14, editorFontSize: 16,
  autoSave: true, autoSaveInterval: 30, showLineNumbers: false,
  lastProject: "", recentProjects: [],
};

export function App() {
  const [connected, setConnected] = useState(false);
  const [version, setVersion] = useState("—");
  const [project, setProject] = useState<OpenedProject | null>(null);
  const [projectV2, setProjectV2] = useState<OpenedProjectV2 | null>(null);
  const [snapshot, setSnapshot] = useState<ProjectSnapshot | null>(null);
  const [manuscript, setManuscript] = useState<ManuscriptSnapshot | null>(null);
  const [activeV2ChapterId, setActiveV2ChapterId] = useState<string | null>(null);
  const [document, setDocument] = useState<DocumentSnapshot | null>(null);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<RpcError | null>(null);
  const [saveConflict, setSaveConflict] = useState<RpcError | null>(null);
  const [view, setView] = useState<View>("write");
  const [query, setQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [createKind, setCreateKind] = useState<CreateKind>("chapter");
  const [createTitle, setCreateTitle] = useState("");
  const [chapterId, setChapterId] = useState("chapter_01");
  const [trashOpen, setTrashOpen] = useState(false);
  const [trash, setTrash] = useState<TrashSnapshot>({ items: [] });
  const [manuscriptTrash, setManuscriptTrash] = useState<ManuscriptTrashSnapshot>({ items: [] });
  const [aiOpen, setAIOpen] = useState(false);
  const [aiTask, setAITask] = useState<AITask | null>(null);
  const [aiResult, setAIResult] = useState<AIResult | null>(null);
  const [aiContextReport, setAIContextReport] = useState<Record<string, unknown> | null>(null);
  const [aiNoticeAccepted, setAINoticeAccepted] = useState(false);
  const [graphRefreshToken, setGraphRefreshToken] = useState(0);
  const [preferences, setPreferences] = useState(DEFAULT_PREFERENCES);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsDraft, setSettingsDraft] = useState(DEFAULT_PREFERENCES);
  const [previewMode, setPreviewMode] = useState(false);
  const [projectCreateOpen, setProjectCreateOpen] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectAuthor, setProjectAuthor] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [appendImportOpen, setAppendImportOpen] = useState(false);
  const [reconstructionOpen, setReconstructionOpen] = useState(false);
  const [reconstructionRefreshToken, setReconstructionRefreshToken] = useState(0);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [knowledgeRefreshToken, setKnowledgeRefreshToken] = useState(0);
  const bootstrapStarted = useRef(false);

  const dirty = document !== null && content !== document.content;
  const characterCount = useMemo(() => content.replace(/\s/g, "").length, [content]);
  const filteredGroups = useMemo(() => {
    if (snapshot === null) return [];
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    return GROUPS.map((group) => ({
      ...group,
      items: (snapshot[group.key] as ProjectDocumentItem[]).filter((item) =>
        !needle || `${item.title} ${item.id} ${item.relativePath}`.toLocaleLowerCase("zh-CN").includes(needle)),
    })).filter((group) => group.items.length > 0);
  }, [query, snapshot]);
  const filteredManuscript = useMemo(() => {
    if (manuscript === null) return [];
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    return manuscript.chapters.filter((item) =>
      !needle || `${item.title} ${item.chapterId} ${item.relativePath}`.toLocaleLowerCase("zh-CN").includes(needle));
  }, [manuscript, query]);

  useEffect(() => {
    void window.novalist.getStatus().then((result) => {
      if (result.ok) { setConnected(result.value.connected); setVersion(result.value.handshake.applicationVersion); }
      else setError(result.error);
    });
    if (!bootstrapStarted.current) {
      bootstrapStarted.current = true;
      void window.novalist.getPreferences().then(async (result) => {
        if (!result.ok) { setError(result.error); return; }
        setPreferences(result.value); setSettingsDraft(result.value);
      });
    }
    return window.novalist.onAppEvent((event) => {
      if (event.event === "sidecar.ready") setConnected(true);
      if (event.event === "sidecar.stopping") setConnected(false);
      if (event.event === "project.opened") {
        setAIOpen(false); setAITask(null); setAIResult(null); setAIContextReport(null);
        setReconstructionOpen(false);
        setKnowledgeOpen(false);
        setProjectV2(null); setManuscript(null); setActiveV2ChapterId(null);
        setProject(event.data.opened);
        void window.novalist.getProjectSnapshot().then(async (result) => {
          if (!result.ok) { setError(result.error); return; }
          setSnapshot(result.value);
          const initial = result.value.chapters[0] ?? result.value.outlines[0];
          if (initial === undefined) return;
          const opened = await window.novalist.openDocument(initial.path, initial.category);
          if (opened.ok) { setDocument(opened.value); setContent(opened.value.content); setView("write"); }
          else setError(opened.error);
        });
      }
      if (event.event === "projectV2.opened") {
        setAIOpen(false); setAITask(null); setAIResult(null); setAIContextReport(null);
        setReconstructionOpen(false); setKnowledgeOpen(false);
        setProject(null); setSnapshot(null); setProjectV2(event.data.opened);
        setDocument(null); setContent(""); setActiveV2ChapterId(null);
        void window.novalist.getManuscriptSnapshot().then(async (result) => {
          if (!result.ok) { setError(result.error); return; }
          setManuscript(result.value);
          const initial = result.value.chapters[0];
          if (initial === undefined) return;
          const opened = await window.novalist.openManuscript(initial.chapterId);
          if (opened.ok) {
            setActiveV2ChapterId(initial.chapterId); setDocument(opened.value);
            setContent(opened.value.content); setView("write"); setPreviewMode(false);
          } else setError(opened.error);
        });
      }
      if (event.event === "project.closed") {
        setAIOpen(false); setAITask(null); setAIResult(null); setAIContextReport(null);
        setProject(null); setProjectV2(null); setSnapshot(null); setManuscript(null);
        setActiveV2ChapterId(null); setDocument(null); setContent(""); setPreviewMode(false); setReconstructionOpen(false); setKnowledgeOpen(false);
      }
      if (event.event === "reconstruction.updated") {
        setReconstructionRefreshToken((value) => value + 1);
        setGraphRefreshToken((value) => value + 1);
      }
      if (event.event === "manuscript.changed" && event.data.reconstructionInvalidated) {
        setReconstructionRefreshToken((value) => value + 1);
        setGraphRefreshToken((value) => value + 1);
      }
      if (event.event === "manuscript.structureChanged") {
        void window.novalist.getManuscriptSnapshot().then((result) => {
          if (result.ok) setManuscript(result.value); else setError(result.error);
        });
        if (event.data.reconstructionInvalidated) {
          setReconstructionRefreshToken((value) => value + 1);
          setGraphRefreshToken((value) => value + 1);
        }
      }
      if (event.event === "knowledge.updated") {
        setKnowledgeRefreshToken((value) => value + 1);
        setReconstructionRefreshToken((value) => value + 1);
        setGraphRefreshToken((value) => value + 1);
      }
      if (event.event === "ai.taskUpdated") {
        setAITask(event.data.task);
        if (event.data.task.status === "succeeded") {
          void window.novalist.getAIResult(event.data.task.taskId).then((result) => {
            if (result.ok) setAIResult(result.value.result); else setError(result.error);
          });
        }
      }
      if (event.event === "ai.contextReport") setAIContextReport(event.data.report);
    });
  }, []);
  useEffect(() => window.novalist.setDocumentDirty(dirty), [dirty]);

  const guardDiscard = useCallback(
    async (action: string) => !dirty || (await window.novalist.confirmDiscardChanges(action)), [dirty]);
  const applyDocument = useCallback((value: DocumentSnapshot) => {
    setDocument(value); setContent(value.content); setError(null); setSaveConflict(null); setView("write"); setPreviewMode(false);
  }, []);
  const loadSnapshot = useCallback(async () => {
    const result = await window.novalist.getProjectSnapshot();
    if (result.ok) setSnapshot(result.value); else setError(result.error);
    return result;
  }, []);
  const openDocument = useCallback(async (item: ProjectDocumentItem, action = "打开其他资料") => {
    if (!(await guardDiscard(action))) return;
    setBusy(true);
    const result = await window.novalist.openDocument(item.path, item.category);
    setBusy(false);
    if (result.ok) applyDocument(result.value); else setError(result.error);
  }, [applyDocument, guardDiscard]);
  const openManuscript = useCallback(async (item: ManuscriptItem, action = "打开其他章节") => {
    if (!(await guardDiscard(action))) return;
    setBusy(true);
    const result = await window.novalist.openManuscript(item.chapterId);
    setBusy(false);
    if (result.ok) { setActiveV2ChapterId(item.chapterId); applyDocument(result.value); }
    else setError(result.error);
  }, [applyDocument, guardDiscard]);
  const openEvidence = useCallback((evidenceChapterId: string) => {
    const item = manuscript?.chapters.find((chapter) => chapter.chapterId === evidenceChapterId);
    if (item !== undefined) void openManuscript(item, "打开知识证据");
  }, [manuscript, openManuscript]);
  const openGraphCharacter = useCallback(async (node: GraphNode) => {
    if (node.path === null || !(await guardDiscard("从图谱打开人物卡"))) return;
    setBusy(true);
    const result = await window.novalist.openDocument(node.path, "人物");
    setBusy(false);
    if (result.ok) applyDocument(result.value); else setError(result.error);
  }, [applyDocument, guardDiscard]);

  const chooseProject = useCallback(async () => {
    if (!(await guardDiscard("打开其他项目"))) return;
    setBusy(true);
    const result = await window.novalist.chooseAndOpenProject();
    if (!result.ok) { setBusy(false); setError(result.error); return; }
    if (result.value === null) { setBusy(false); return; }
    setProject(result.value); setDocument(null); setContent(""); setError(null);
    const loaded = await window.novalist.getProjectSnapshot();
    setBusy(false);
    if (!loaded.ok) { setError(loaded.error); return; }
    setSnapshot(loaded.value);
    const initial = loaded.value.chapters[0] ?? loaded.value.outlines[0];
    if (initial !== undefined) void openDocument(initial, "打开项目");
  }, [guardDiscard, openDocument]);

  const chooseProjectV2 = useCallback(async () => {
    if (!(await guardDiscard("打开其他项目"))) return;
    setBusy(true);
    const result = await window.novalist.chooseAndOpenProjectV2();
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    if (result.value !== null) { setProjectV2(result.value); setError(null); }
  }, [guardDiscard]);

  const createProject = async (event: FormEvent) => {
    event.preventDefault();
    if (!projectName.trim() || !(await guardDiscard("新建项目"))) return;
    setBusy(true);
    const result = await window.novalist.createProject(projectName, projectAuthor);
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    if (result.value === null) return;
    setProjectCreateOpen(false); setProjectName(""); setProjectAuthor("");
    setProject(result.value); setDocument(null); setContent(""); setError(null);
  };

  const savePreferences = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    const result = await window.novalist.updatePreferences({
      theme: settingsDraft.theme,
      uiFontSize: settingsDraft.uiFontSize,
      editorFontSize: settingsDraft.editorFontSize,
      autoSave: settingsDraft.autoSave,
      autoSaveInterval: settingsDraft.autoSaveInterval,
      showLineNumbers: settingsDraft.showLineNumbers,
    });
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    setPreferences(result.value); setSettingsDraft(result.value); setSettingsOpen(false);
  };

  const saveDocument = useCallback(async (force = false) => {
    if (document === null) return;
    setBusy(true); setSaveConflict(null);
    const result = projectV2 !== null && activeV2ChapterId !== null
      ? await window.novalist.saveManuscript({
        chapterId: activeV2ChapterId, content, expectedRevision: document.revision, force,
      } satisfies SaveManuscriptInput)
      : await window.novalist.saveDocument({
        path: document.path, category: document.category, content,
        expectedRevision: document.revision, force,
      } satisfies SaveDocumentInput);
    setBusy(false);
    if (result.ok) {
      applyDocument(result.value);
      if (projectV2 !== null) {
        const refreshed = await window.novalist.getManuscriptSnapshot();
        if (refreshed.ok) setManuscript(refreshed.value); else setError(refreshed.error);
      }
    }
    else if (result.error.code === "REVISION_CONFLICT") setSaveConflict(result.error);
    else setError(result.error);
  }, [activeV2ChapterId, applyDocument, content, document, projectV2]);

  const closeProject = useCallback(async () => {
    if (!(await guardDiscard("关闭项目"))) return;
    const result = await window.novalist.closeProject();
    if (!result.ok) { setError(result.error); return; }
    setProject(null); setProjectV2(null); setSnapshot(null); setManuscript(null);
    setActiveV2ChapterId(null); setDocument(null); setContent(""); setView("write"); setError(null);
    setReconstructionOpen(false); setKnowledgeOpen(false);
  }, [guardDiscard]);

  const openCreate = (kind: CreateKind) => {
    setCreateKind(kind); setCreateTitle(""); setChapterId(snapshot?.nextChapterId ?? "chapter_01"); setCreateOpen(true);
  };
  const createDocument = async (event: FormEvent) => {
    event.preventDefault();
    if (!createTitle.trim()) return;
    if (!(await guardDiscard(projectV2 !== null ? "新建章节" : "新建资料"))) return;
    if (projectV2 !== null) {
      setBusy(true);
      const result = await window.novalist.createManuscript(createTitle, activeV2ChapterId ?? undefined);
      setBusy(false);
      if (!result.ok) { setError(result.error); return; }
      setManuscript(result.value.snapshot); setCreateOpen(false);
      if (result.value.document !== undefined) {
        const created = result.value.snapshot.chapters.find(
          (item) => item.relativePath === result.value.document?.relativePath,
        );
        setActiveV2ChapterId(created?.chapterId ?? null);
        applyDocument(result.value.document);
      }
      return;
    }
    setBusy(true);
    const result = createKind === "chapter"
      ? await window.novalist.createChapter(createTitle, chapterId)
      : await window.novalist.createCanonEntry(createKind, createTitle);
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    setSnapshot(result.value.snapshot); setCreateOpen(false);
    const created = allItems(result.value.snapshot).find((item) => samePath(item.path, result.value.mutation.resultPath));
    if (created !== undefined) void openDocument(created, "打开新建资料");
  };
  const createTimeline = async () => {
    setBusy(true); const result = await window.novalist.createTimeline(); setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    setSnapshot(result.value.snapshot);
    const created = result.value.snapshot.timeline[0];
    if (created !== undefined) void openDocument(created, "打开时间线");
  };
  const importMarkdown = async () => {
    setBusy(true); const result = await window.novalist.importMarkdown(); setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    if (result.value === null) return;
    setSnapshot(result.value.snapshot);
    const imported = result.value.imported[0];
    const created = imported === undefined ? undefined : allItems(result.value.snapshot).find((item) => samePath(item.path, imported.resultPath));
    if (created !== undefined) void openDocument(created, "打开导入章节");
  };
  const deleteDocument = async (item: ProjectDocumentItem) => {
    if (document !== null && samePath(document.path, item.path) && !(await guardDiscard("删除当前资料"))) return;
    setBusy(true); const result = await window.novalist.deleteDocument(item); setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    if (result.value === null) return;
    setSnapshot(result.value.snapshot);
    if (document !== null && samePath(document.path, item.path)) { setDocument(null); setContent(""); }
  };
  const renameManuscript = async (item: ManuscriptItem) => {
    if (!(await guardDiscard("重命名章节"))) return;
    const title = window.prompt("新的章节标题", item.title)?.trim();
    if (!title || title === item.title) return;
    setBusy(true);
    const opened = await window.novalist.openManuscript(item.chapterId);
    if (!opened.ok) { setBusy(false); setError(opened.error); return; }
    const result = await window.novalist.renameManuscript(item.chapterId, title, opened.value.revision);
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    setManuscript(result.value.snapshot);
    if (result.value.document !== undefined) {
      setActiveV2ChapterId(item.chapterId); applyDocument(result.value.document);
    }
  };
  const moveManuscript = async (item: ManuscriptItem, direction: -1 | 1) => {
    if (manuscript === null) return;
    const ids = manuscript.chapters.map((chapter) => chapter.chapterId);
    const from = ids.indexOf(item.chapterId); const to = from + direction;
    if (from < 0 || to < 0 || to >= ids.length) return;
    const moved = ids[from]; const displaced = ids[to];
    if (moved === undefined || displaced === undefined) return;
    ids[from] = displaced; ids[to] = moved;
    setBusy(true); const result = await window.novalist.reorderManuscript(ids); setBusy(false);
    if (result.ok) setManuscript(result.value.snapshot); else setError(result.error);
  };
  const deleteManuscript = async (item: ManuscriptItem) => {
    if (activeV2ChapterId === item.chapterId && !(await guardDiscard("删除当前章节"))) return;
    if (!window.confirm(`将“${item.title}”移入正文回收站？`)) return;
    setBusy(true); const result = await window.novalist.deleteManuscript(item.chapterId); setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    setManuscript(result.value.snapshot);
    if (result.value.trash !== undefined) setManuscriptTrash(result.value.trash);
    if (activeV2ChapterId === item.chapterId) {
      const next = result.value.snapshot.chapters[Math.min(item.sequence - 1, result.value.snapshot.chapters.length - 1)];
      setActiveV2ChapterId(null); setDocument(null); setContent("");
      if (next !== undefined) void openManuscript(next, "打开相邻章节");
    }
  };
  const toggleImportance = async (item: ProjectDocumentItem) => {
    const importance = item.importance === "core" ? "non_core" : "core";
    const result = await window.novalist.setSystemImportance(item.path, importance);
    if (result.ok) setSnapshot(result.value); else setError(result.error);
  };
  const showTrash = useCallback(async () => {
    if (projectV2 !== null) {
      const result = await window.novalist.getManuscriptTrash();
      if (!result.ok) { setError(result.error); return; }
      setManuscriptTrash(result.value); setTrashOpen(true); return;
    }
    const result = await window.novalist.getTrash();
    if (!result.ok) { setError(result.error); return; }
    setTrash(result.value); setTrashOpen(true);
  }, [projectV2]);
  const restoreManuscriptTrash = async (item: ManuscriptTrashItem) => {
    if (!(await guardDiscard("恢复并打开章节"))) return;
    setBusy(true); const result = await window.novalist.restoreManuscriptTrash(item.trashId); setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    setManuscript(result.value.snapshot);
    if (result.value.trash !== undefined) setManuscriptTrash(result.value.trash);
    if (result.value.document !== undefined) {
      setActiveV2ChapterId(item.chapterId); applyDocument(result.value.document);
    }
  };
  const deleteManuscriptTrashForever = async (item: ManuscriptTrashItem) => {
    if (!window.confirm(`永久删除“${item.title}”？此操作不可恢复。`)) return;
    setBusy(true); const result = await window.novalist.deleteManuscriptTrashForever(item.trashId); setBusy(false);
    if (result.ok) setManuscriptTrash(result.value); else setError(result.error);
  };
  const openAppendImport = async () => {
    if (!(await guardDiscard("追加导入并打开章节"))) return;
    setAppendImportOpen(true);
  };
  const applyAppendedManuscript = (result: ManuscriptMutationResult) => {
    setAppendImportOpen(false); setManuscript(result.snapshot);
    if (result.document !== undefined) {
      const imported = result.snapshot.chapters.find(
        (item) => item.relativePath === result.document?.relativePath,
      );
      setActiveV2ChapterId(imported?.chapterId ?? null);
      applyDocument(result.document);
    }
  };
  const exportManuscript = async (format: "md" | "txt") => {
    if (dirty) {
      setError(localError("UNSAVED_DOCUMENT", "请先保存当前章节，再导出全书。")); return;
    }
    setBusy(true); const result = await window.novalist.exportManuscript(format); setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    if (result.value !== null) {
      window.alert(`已导出 ${result.value.chapterCount} 章\n${result.value.path}`);
    }
  };
  const restoreTrash = async (item: TrashItem, policy: "error" | "rename" = "error") => {
    setBusy(true); const result = await window.novalist.restoreTrash(item.kind, item.trashId, policy); setBusy(false);
    if (!result.ok) {
      if (result.error.code === "ID_CONFLICT" && item.canRename && window.confirm("原位置已有同名资料。要使用新名称恢复吗？")) void restoreTrash(item, "rename");
      else setError(result.error);
      return;
    }
    setSnapshot(result.value.snapshot); setTrash(result.value.trash);
  };
  const deleteForever = async (item: TrashItem) => {
    setBusy(true); const result = await window.novalist.deleteTrashForever(item); setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    if (result.value !== null) setTrash(result.value);
  };
  const currentChapter = snapshot?.chapters.find((item) => document !== null && samePath(item.path, document.path));
  const currentAIChapterId = projectV2 !== null ? activeV2ChapterId : currentChapter?.id ?? null;
  const aiRunning = aiTask !== null && ["queued", "running", "cancel_requested"].includes(aiTask.status);
  const showAI = async () => {
    setAIOpen(true);
    const result = await window.novalist.getAIStatus();
    if (!result.ok) { setError(result.error); return; }
    setAITask(result.value.active ?? result.value.recent[0] ?? null);
    const review = result.value.recent.find((item) => item.hasResult);
    if (review !== undefined) {
      const loaded = await window.novalist.getAIResult(review.taskId);
      if (loaded.ok) { setAITask(loaded.value.task); setAIResult(loaded.value.result); }
    }
  };
  const startAI = async (kind: AITaskKind) => {
    if (aiRunning) return;
    if (kind !== "connection" && ((project === null && projectV2 === null) || document === null || currentAIChapterId === null)) {
      setError(localError("AI_CHAPTER_REQUIRED", "请先打开一个章节，再启动创作任务。")); return;
    }
    if (dirty) { setError(localError("UNSAVED_DOCUMENT", "请先保存当前章节，再启动 AI 任务。")); return; }
    let accepted = aiNoticeAccepted;
    if (!accepted) {
      accepted = window.confirm(projectV2 !== null
        ? "AI 数据处理告知\n\n任务会把当前章节、有限前文、已审核的 schema-v2 知识及已审核且版本有效的章节记忆发送给你配置的 DeepSeek Harness。不会读取旧项目人物卡、世界观、大纲或记忆。AI 输出可能不准确，采用前必须人工审阅。\n\n是否继续本次会话？"
        : "AI 数据处理告知\n\n任务会把所选章节及完成任务所需的本地大纲、人物、世界观、体系、时间线和故事记忆发送给你配置的 DeepSeek Harness。AI 输出可能不准确，采用前必须人工审阅。\n\n是否继续本次会话？");
      if (!accepted) return;
      setAINoticeAccepted(true);
    }
    setAIResult(null); setAIContextReport(null); setAIOpen(true);
    const result = await window.novalist.startAITask({
      kind,
      chapterId: kind === "connection" ? "" : currentAIChapterId!,
      sourceRevision: kind === "connection" ? null : document!.revision,
      noticeAccepted: accepted,
    });
    if (result.ok) setAITask(result.value); else setError(result.error);
  };
  const cancelAI = async () => {
    if (aiTask === null) return;
    const result = await window.novalist.cancelAITask(aiTask.taskId);
    if (result.ok) setAITask(result.value); else setError(result.error);
  };
  const applyAI = async () => {
    if (aiTask === null || aiResult === null) return;
    if (aiResult.type === "writing") {
      const result = await window.novalist.applyAIWritingResult(aiTask.taskId);
      if (!result.ok) { setError(result.error); return; }
      if (result.value !== null) { applyDocument(result.value); setAIResult(null); }
    } else if (aiResult.type === "memory") {
      if (aiResult.hasBlockers) { setError(localError("MEMORY_BLOCKED", "提案仍有阻断冲突，不能采用。")); return; }
      const result = await window.novalist.commitAIMemoryResult(aiTask.taskId);
      if (!result.ok) setError(result.error); else if (result.value !== null) setAIResult(null);
    }
  };
  const discardAI = async () => {
    if (aiTask === null) return;
    const result = await window.novalist.discardAIResult(aiTask.taskId);
    if (result.ok) { setAITask(result.value); setAIResult(null); } else setError(result.error);
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault(); if (dirty && !busy) void saveDocument();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, dirty, saveDocument]);
  useEffect(() => {
    if (!preferences.autoSave || previewMode || !dirty || busy || saveConflict !== null || document === null) return;
    const timer = window.setTimeout(() => void saveDocument(), preferences.autoSaveInterval * 1000);
    return () => window.clearTimeout(timer);
  }, [busy, dirty, document, preferences.autoSave, preferences.autoSaveInterval, previewMode, saveConflict, saveDocument]);
  useEffect(() => {
    if (project === null) return;
    return window.novalist.onAppEvent((event) => {
      if (event.event === "project.contentChanged") {
        void loadSnapshot();
        setGraphRefreshToken((value) => value + 1);
      }
      if (event.event === "trash.changed" && trashOpen) void showTrash();
    });
  }, [loadSnapshot, project, showTrash, trashOpen]);

  return <main className={`app-shell theme-${preferences.theme}`} style={{ fontSize: `${preferences.uiFontSize}px` }}>
    <header className="topbar">
      <div className="brand-lockup"><img className="brand-mark" src={brandIconUrl} alt="" /><div><strong>DeepSonder</strong><span>Electron</span></div></div>
      <div className="project-heading"><strong>{projectV2?.name ?? project?.project.name ?? "尚未打开项目"}</strong><span>{projectV2?.root ?? project?.project.root ?? "导入正文建立项目，或打开已有 v2 项目"}</span></div>
      <div className="topbar-actions"><span className={`connection ${connected ? "online" : "offline"}`}><i /> {connected ? `Sidecar ${version}` : "Sidecar 离线"}</span><button className="primary-button" onClick={() => setImportOpen(true)} disabled={busy}>导入正文</button><button className="secondary-button" onClick={() => void chooseProjectV2()} disabled={busy}>打开 v2 项目</button>{(projectV2 !== null || project !== null) && <button className="ghost-button" onClick={() => void closeProject()} disabled={busy}>关闭</button>}</div>
    </header>
    <section className="workspace">
      <aside className="rail" aria-label="主导航">
        <button className={view === "write" ? "rail-item active" : "rail-item"} onClick={() => setView("write")}><span>✎</span>写作</button>
        <button className={view === "graph" ? "rail-item active" : "rail-item"} onClick={() => setView("graph")} disabled={project === null && projectV2 === null}><span>⌘</span>图谱</button>
        <button className={reconstructionOpen ? "rail-item active" : "rail-item"} onClick={() => { setKnowledgeOpen(false); setReconstructionOpen(true); }} disabled={projectV2 === null}><span>◇</span>识别</button>
        <button className={knowledgeOpen ? "rail-item active" : "rail-item"} onClick={() => { setReconstructionOpen(false); setKnowledgeOpen(true); }} disabled={projectV2 === null}><span>◎</span>知识</button>
        <button className={aiOpen ? "rail-item active" : "rail-item"} onClick={() => void showAI()} disabled={project === null && projectV2 === null}><span>✦</span>AI</button>
        <button className={settingsOpen ? "rail-item active settings-rail" : "rail-item settings-rail"} onClick={() => { setSettingsDraft(preferences); setSettingsOpen(true); }}><span>⚙</span>设置</button>
        <button className="rail-item trash-rail" onClick={() => void showTrash()} disabled={project === null && projectV2 === null}><span>♲</span>回收站</button>
      </aside>
      <aside className="document-panel">
        <div className="panel-heading"><div><span className="eyebrow">{projectV2 !== null ? "MANUSCRIPT V2" : "PROJECT LIBRARY"}</span><h2>{projectV2 !== null ? "正文目录" : "创作资料"}</h2></div>{(projectV2 !== null || project !== null) && <button className="icon-button" title={projectV2 !== null ? "新建章节" : "新建资料"} onClick={() => openCreate("chapter")} disabled={busy}>＋</button>}</div>
        {projectV2 !== null && manuscript !== null ? <div className="library-body">
          <div className="library-tools"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索章节…" aria-label="搜索正文章节" /><div className="quick-actions"><button onClick={() => void openAppendImport()} disabled={busy}>追加导入</button><button onClick={() => void exportManuscript("md")} disabled={busy || manuscript.itemCount === 0}>导出 MD</button><button onClick={() => void exportManuscript("txt")} disabled={busy || manuscript.itemCount === 0}>导出 TXT</button></div><p className="v2-library-note">人物与世界观不会沿用旧资料，将在后续识别流程中重新建立。</p></div>
          <nav className="document-list">
            <section className="document-group"><h3><span>章节</span><small>{filteredManuscript.length}</small></h3>{filteredManuscript.map((item) => <div className={activeV2ChapterId === item.chapterId ? "document-row active" : "document-row"} key={item.chapterId}><button className="document-item" onClick={() => void openManuscript(item)} disabled={busy}><span className="file-glyph">文</span><span><strong>{item.title}</strong><small>{item.chapterId}</small></span></button><div className="chapter-row-actions"><button title="上移" onClick={() => void moveManuscript(item, -1)} disabled={busy || item.sequence === 1}>↑</button><button title="下移" onClick={() => void moveManuscript(item, 1)} disabled={busy || item.sequence === (manuscript?.itemCount ?? 0)}>↓</button><button title="重命名" onClick={() => void renameManuscript(item)} disabled={busy}>✎</button><button className="row-delete" title="移入正文回收站" onClick={() => void deleteManuscript(item)} disabled={busy}>×</button></div></div>)}</section>
            {filteredManuscript.length === 0 && <div className="search-empty">没有匹配的章节</div>}
          </nav>
        </div> : project === null ? <div className="panel-empty">导入正文建立新项目，或打开已有 v2 项目。</div> : <div className="library-body">
          <div className="library-tools"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索资料…" aria-label="搜索项目资料" /><div className="quick-actions"><button onClick={() => openCreate("chapter")}>章节</button><button onClick={() => openCreate("character")}>角色</button><button onClick={() => openCreate("world")}>世界</button><button onClick={() => openCreate("power")}>体系</button><button onClick={() => void importMarkdown()}>导入</button></div></div>
          <nav className="document-list">
            {filteredGroups.map((group) => <section className="document-group" key={group.key}><h3><span>{group.label}</span><small>{group.items.length}</small></h3>{group.items.map((item) => <div className={document?.relativePath === item.relativePath ? "document-row active" : "document-row"} key={item.path}><button className="document-item" onClick={() => void openDocument(item)} disabled={busy}><span className="file-glyph">{KIND_LABEL[item.kind].slice(0, 1)}</span><span><strong>{item.title}</strong><small>{item.id}</small></span></button>{item.kind === "power" && !item.protected && <button className={`importance ${item.importance === "core" ? "core" : ""}`} title="切换核心体系" onClick={() => void toggleImportance(item)}>◆</button>}{isDeletable(item) && <button className="row-delete" title="移入回收站" onClick={() => void deleteDocument(item)}>×</button>}</div>)}</section>)}
            {snapshot?.timeline.length === 0 && <button className="create-timeline" onClick={() => void createTimeline()}>＋ 建立时间线</button>}
            {filteredGroups.length === 0 && <div className="search-empty">没有匹配的资料</div>}
          </nav>
        </div>}
        <div className="panel-footer"><span>{projectV2 !== null ? "Schema v2" : "RPC v1"}</span><span>{projectV2 !== null ? `${manuscript?.itemCount ?? 0} 章正文` : snapshot === null ? "只读待机" : `${snapshot.itemCount} 项资料`}</span></div>
      </aside>
      <section className="content-surface">
        {error !== null && <div className="error-banner" role="alert"><div><strong>{error.code}</strong><span>{error.message}</span></div><button onClick={() => setError(null)}>关闭</button></div>}
        {saveConflict !== null && <div className="conflict-banner" role="alert"><div><strong>磁盘上的文件已经变化</strong><span>继续覆盖会丢失外部版本。请确认后再操作。</span></div><button onClick={() => void saveDocument(true)} disabled={busy}>确认覆盖</button><button onClick={() => setSaveConflict(null)}>返回检查</button></div>}
        {view === "graph" && (project !== null || projectV2 !== null) ? <Suspense fallback={<div className="feature-loading">正在加载关系图谱…</div>}><GraphView projectRoot={projectV2?.root ?? project!.project.root} refreshToken={graphRefreshToken} onOpenCharacter={(node) => void openGraphCharacter(node)} onOpenEvidence={openEvidence} onError={setError} /></Suspense> : document === null ? <Welcome onOpen={() => void chooseProjectV2()} onImport={() => setImportOpen(true)} busy={busy} /> : <><div className="editor-header"><div><span className="eyebrow">{document.category}</span><h1>{document.title}</h1><p>{document.relativePath}</p></div><div className="editor-actions"><div className="view-toggle"><button className={!previewMode ? "active" : ""} onClick={() => setPreviewMode(false)}>源码</button><button className={previewMode ? "active" : ""} onClick={() => setPreviewMode(true)}>预览</button></div><span className={dirty ? "save-state dirty" : "save-state"}>{dirty ? (preferences.autoSave ? "等待自动保存" : "有未保存修改") : "已安全保存"}</span><button className="primary-button" onClick={() => void saveDocument()} disabled={!dirty || busy}>{busy ? "处理中…" : "保存  Ctrl+S"}</button></div></div>{previewMode ? <MarkdownPreview content={content} /> : <Suspense fallback={<div className="feature-loading">正在加载编辑器…</div>}><MarkdownEditor key={document.path} value={content} onChange={setContent} fontSize={preferences.editorFontSize} showLineNumbers={preferences.showLineNumbers} /></Suspense>}<footer className="editor-footer"><span>Markdown · UTF-8 · {preferences.autoSave ? `${preferences.autoSaveInterval} 秒自动保存` : "手动保存"}</span><span>{characterCount.toLocaleString("zh-CN")} 字符</span></footer></>}
      </section>
    </section>
    {createOpen && <div className="modal-layer" role="presentation"><form className="dialog-card" onSubmit={(event) => void createDocument(event)}><span className="eyebrow">{projectV2 !== null ? "NEW MANUSCRIPT CHAPTER" : "NEW PROJECT MATERIAL"}</span><h2>新建{KIND_LABEL[createKind]}</h2>{projectV2 === null && <label>资料类型<select value={createKind} onChange={(event) => setCreateKind(event.target.value as CreateKind)}><option value="chapter">章节</option><option value="character">角色</option><option value="world">世界观</option><option value="power">体系</option></select></label>}<label>名称<input autoFocus maxLength={200} value={createTitle} onChange={(event) => setCreateTitle(event.target.value)} placeholder="输入章节标题" /></label>{projectV2 === null && createKind === "chapter" && <label>文件标识<input value={chapterId} onChange={(event) => setChapterId(event.target.value)} /></label>}{projectV2 !== null && <p className="dialog-hint">新章节将插入到当前章节之后；未选中章节时追加到末尾。</p>}<div className="dialog-actions"><button type="button" className="ghost-button" onClick={() => setCreateOpen(false)}>取消</button><button className="primary-button" disabled={busy || !createTitle.trim()}>创建</button></div></form></div>}
    {projectCreateOpen && <div className="modal-layer" role="presentation"><form className="dialog-card" onSubmit={(event) => void createProject(event)}><span className="eyebrow">NEW DEEPSONDER PROJECT</span><h2>建立新项目</h2><p className="dialog-hint">提交后选择父目录；应用会在其中创建完整、兼容现有版本的项目文件夹。</p><label>项目名称<input autoFocus value={projectName} maxLength={200} onChange={(event) => setProjectName(event.target.value)} placeholder="例如：雾港来信" /></label><label>作者<input value={projectAuthor} maxLength={500} onChange={(event) => setProjectAuthor(event.target.value)} placeholder="可选" /></label><div className="dialog-actions"><button type="button" className="ghost-button" onClick={() => setProjectCreateOpen(false)}>取消</button><button className="primary-button" disabled={busy || !projectName.trim()}>选择位置并创建</button></div></form></div>}
    {importOpen && <ImportWizard onClose={() => setImportOpen(false)} onCreated={(opened) => { setImportOpen(false); setProjectV2(opened); }} onError={setError} />}
    {appendImportOpen && projectV2 !== null && <ImportWizard mode="append" {...(activeV2ChapterId === null ? {} : { afterChapterId: activeV2ChapterId })} onClose={() => setAppendImportOpen(false)} onAppended={applyAppendedManuscript} onError={setError} />}
    {reconstructionOpen && projectV2 !== null && <ReconstructionDrawer refreshToken={reconstructionRefreshToken} documentDirty={dirty} onClose={() => setReconstructionOpen(false)} onError={setError} onKnowledgeChanged={() => setGraphRefreshToken((value) => value + 1)} onOpenEvidence={openEvidence} />}
    {knowledgeOpen && projectV2 !== null && <KnowledgeDrawer refreshToken={knowledgeRefreshToken} onClose={() => setKnowledgeOpen(false)} onError={setError} onChanged={() => setGraphRefreshToken((value) => value + 1)} onOpenEvidence={openEvidence} />}
    {trashOpen && <div className="modal-layer" role="presentation"><section className="trash-card"><header><div><span className="eyebrow">PROJECT RECYCLE BIN</span><h2>{projectV2 !== null ? "正文回收站" : "项目回收站"}</h2></div><button className="icon-button" onClick={() => setTrashOpen(false)}>×</button></header><p>删除的内容保存在当前项目中。永久删除后不可恢复。</p>{projectV2 !== null ? <div className="trash-list">{manuscriptTrash.items.length === 0 ? <div className="trash-empty">回收站是空的</div> : manuscriptTrash.items.map((item) => <article key={item.trashId}><div><strong>{item.title}</strong><span>第 {item.sequence} 章 · {formatDeletedAt(item.deletedAt)}</span><small>{item.chapterId}</small></div><button onClick={() => void restoreManuscriptTrash(item)} disabled={busy}>恢复</button><button className="danger-button" onClick={() => void deleteManuscriptTrashForever(item)} disabled={busy}>永久删除</button></article>)}</div> : <div className="trash-list">{trash.items.length === 0 ? <div className="trash-empty">回收站是空的</div> : trash.items.map((item) => <article key={item.trashId}><div><strong>{item.title}</strong><span>{KIND_LABEL[item.kind]} · {formatDeletedAt(item.deletedAt)}</span><small>{item.originalPath}</small></div><button onClick={() => void restoreTrash(item)} disabled={busy}>恢复</button><button className="danger-button" onClick={() => void deleteForever(item)} disabled={busy}>永久删除</button></article>)}</div>}</section></div>}
    {aiOpen && <aside className="ai-drawer" aria-label="AI 任务面板">
      <header><div><span className="eyebrow">REVIEW-FIRST AI</span><h2>AI 工作流</h2></div><button className="icon-button" onClick={() => setAIOpen(false)}>×</button></header>
      <p className="ai-scope">{projectV2 !== null ? "仅使用当前正文、有限前文与已审核的 v2 知识；旧项目资料不会进入上下文。" : "任务只读取完成当前工作所需的项目资料；生成结果不会自动写入。"}</p>
      <div className="ai-actions"><button onClick={() => void startAI("expand")} disabled={aiRunning || currentAIChapterId === null}>扩写正文</button><button onClick={() => void startAI("continuation")} disabled={aiRunning || currentAIChapterId === null}>续写章节</button><button onClick={() => void startAI("check")} disabled={aiRunning || currentAIChapterId === null}>一致性检查</button><button onClick={() => void startAI("memory")} disabled={aiRunning || currentAIChapterId === null}>生成记忆提案</button><button onClick={() => void startAI("connection")} disabled={aiRunning}>测试 DSH</button></div>
      {aiTask !== null && <section className={`task-status ${aiTask.status}`}><div><strong>{aiKindLabel(aiTask.kind)}</strong><span>{aiTask.stage}</span></div><small>{aiTask.chapterId || "本机配置"}</small><div className="progress-track"><i style={{ width: `${aiTask.progress}%` }} /></div>{aiTask.error && <p>{aiTask.error}</p>}{aiRunning && <button onClick={() => void cancelAI()} disabled={aiTask.status === "cancel_requested"}>{aiTask.status === "cancel_requested" ? "正在取消…" : "取消任务"}</button>}</section>}
      {aiResult !== null && <section className="ai-result"><span className="eyebrow">RESULT PREVIEW</span>{aiResult.type === "writing" && <><h3>{aiResult.mode === "replace" ? "扩写草稿" : "续写草稿"}</h3><p className="result-meta">{aiResult.charCount} 字 · {aiResult.lengthStatus}</p><pre>{aiResult.text}</pre>{aiResult.warning && <p className="result-warning">{aiResult.warning}</p>}<div className="result-actions"><button className="ghost-button" onClick={() => void discardAI()}>放弃</button><button className="primary-button" onClick={() => void applyAI()}>审阅后采用</button></div></>}{aiResult.type === "consistency" && <><h3>一致性报告</h3><pre>{aiResult.formatted}</pre><div className="result-actions"><button className="secondary-button" onClick={() => void discardAI()}>关闭报告</button></div></>}{aiResult.type === "memory" && <><h3>故事记忆提案</h3><p>{aiResult.summary}</p><pre>{aiResult.preview}</pre><p className="result-meta">{aiResult.patchCount} 项变更 · {aiResult.conflictCount} 项冲突</p><div className="result-actions"><button className="ghost-button" onClick={() => void discardAI()}>放弃</button><button className="primary-button" onClick={() => void applyAI()} disabled={aiResult.hasBlockers}>确认采用</button></div></>}{aiResult.type === "connection" && <><h3>DSH 连接测试</h3><pre>{aiResult.message}</pre><div className="result-actions"><button className="secondary-button" onClick={() => void discardAI()}>完成</button></div></>}</section>}
      {aiContextReport !== null && <details className="context-report"><summary>本次上下文用量</summary><span>健康度：{String(aiContextReport.health ?? "unknown")}</span><span>输入估算：{String(aiContextReport.estimated_input_tokens ?? 0)} / {String(aiContextReport.input_token_budget ?? 0)} tokens</span><span>传输：{String(aiContextReport.transport ?? "pending")}</span></details>}
    </aside>}
    {settingsOpen && <aside className="settings-drawer" aria-label="界面设置"><form onSubmit={(event) => void savePreferences(event)}><header><div><span className="eyebrow">LOCAL PREFERENCES</span><h2>界面与编辑器</h2></div><button type="button" className="icon-button" onClick={() => setSettingsOpen(false)}>×</button></header><label>外观<select value={settingsDraft.theme} onChange={(event) => setSettingsDraft({ ...settingsDraft, theme: event.target.value as "light" | "dark" })}><option value="light">浅色</option><option value="dark">深色</option></select></label><label>界面字号 <output>{settingsDraft.uiFontSize}px</output><input type="range" min="10" max="22" value={settingsDraft.uiFontSize} onChange={(event) => setSettingsDraft({ ...settingsDraft, uiFontSize: Number(event.target.value) })} /></label><label>编辑器字号 <output>{settingsDraft.editorFontSize}px</output><input type="range" min="12" max="36" value={settingsDraft.editorFontSize} onChange={(event) => setSettingsDraft({ ...settingsDraft, editorFontSize: Number(event.target.value) })} /></label><label className="check-setting"><input type="checkbox" checked={settingsDraft.autoSave} onChange={(event) => setSettingsDraft({ ...settingsDraft, autoSave: event.target.checked })} /><span>自动保存</span></label><label>自动保存间隔（秒）<input type="number" min="5" max="600" disabled={!settingsDraft.autoSave} value={settingsDraft.autoSaveInterval} onChange={(event) => setSettingsDraft({ ...settingsDraft, autoSaveInterval: Number(event.target.value) })} /></label><label className="check-setting"><input type="checkbox" checked={settingsDraft.showLineNumbers} onChange={(event) => setSettingsDraft({ ...settingsDraft, showLineNumbers: event.target.checked })} /><span>显示编辑器行号</span></label><p className="settings-note">只保存界面偏好；AI 凭据等敏感配置仍由现有安全配置流程管理。</p><div className="dialog-actions"><button type="button" className="ghost-button" onClick={() => setSettingsOpen(false)}>取消</button><button className="primary-button" disabled={busy}>保存设置</button></div></form></aside>}
  </main>;
}

function allItems(snapshot: ProjectSnapshot): ProjectDocumentItem[] { return [...snapshot.chapters, ...snapshot.outlines, ...snapshot.characters, ...snapshot.world, ...snapshot.power, ...snapshot.timeline]; }
function samePath(left: string, right: string): boolean { return left.replaceAll("\\", "/").toLocaleLowerCase() === right.replaceAll("\\", "/").toLocaleLowerCase(); }
function isDeletable(item: ProjectDocumentItem): boolean { return !item.protected && ["chapter", "character", "world", "power", "timeline"].includes(item.kind); }
function formatDeletedAt(value: string): string { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false }); }
function localError(code: string, message: string): RpcError { return { code, message, retryable: false }; }
function aiKindLabel(kind: AITaskKind): string { return ({ expand: "扩写正文", continuation: "续写章节", check: "一致性检查", memory: "故事记忆", connection: "DSH 连接测试" })[kind]; }

function Welcome({ onOpen, onImport, busy }: { onOpen: () => void; onImport: () => void; busy: boolean }) {
  return <div className="welcome"><span className="welcome-kicker">A QUIET PLACE FOR LONG STORIES</span><h1>从正文出发，<br />重新理解你的故事。</h1><p>Electron 是新版唯一入口。导入只接收正文；旧人物卡、世界观和记忆不会复制，后续将依据文本证据重新识别与构建。</p><div className="welcome-actions"><button className="primary-button large" onClick={onImport} disabled={busy}>导入正文建立项目</button><button className="secondary-button large" onClick={onOpen} disabled={busy}>打开 v2 项目</button></div><div className="welcome-grid"><article><strong>正文优先</strong><span>旧项目仅提取章节正文，避免历史结构污染</span></article><article><strong>冲突安全</strong><span>外部修改不会被静默覆盖</span></article><article><strong>证据重建</strong><span>人物与关系将从正文溯源建立</span></article></div></div>;
}

import { contextBridge, ipcRenderer } from "electron";
import type {
  AIResultEnvelope,
  AIStartInput,
  AIStatus,
  AITask,
  AppEvent,
  DocumentSnapshot,
  ImportResult,
  KnowledgeCardDocument,
  KnowledgeSnapshot,
  MutationResult,
  ManuscriptImportPlan,
  ManuscriptSnapshot,
  NovalistBridge,
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
  SaveDocumentInput,
  SaveManuscriptInput,
  TrashItem,
  TrashSnapshot,
} from "../shared/contracts.js";

const bridge: NovalistBridge = {
  getStatus: () => ipcRenderer.invoke("novalist:get-status") as Promise<OperationResult<PreviewStatus>>,
  chooseAndOpenProject: () =>
    ipcRenderer.invoke("novalist:choose-project") as Promise<
      OperationResult<OpenedProject | null>
    >,
  createProject: (name, author) =>
    ipcRenderer.invoke("novalist:create-project", name, author) as Promise<
      OperationResult<OpenedProject | null>
    >,
  chooseAndScanManuscript: (sourceType) =>
    ipcRenderer.invoke("novalist:choose-scan-manuscript", sourceType) as Promise<
      OperationResult<ManuscriptImportPlan | null>
    >,
  createProjectV2: (name, author, planDigest) =>
    ipcRenderer.invoke("novalist:create-project-v2", name, author, planDigest) as Promise<
      OperationResult<OpenedProjectV2 | null>
    >,
  chooseAndOpenProjectV2: () =>
    ipcRenderer.invoke("novalist:choose-project-v2") as Promise<
      OperationResult<OpenedProjectV2 | null>
    >,
  getManuscriptSnapshot: () =>
    ipcRenderer.invoke("novalist:get-manuscript-snapshot") as Promise<
      OperationResult<ManuscriptSnapshot>
    >,
  openManuscript: (chapterId) =>
    ipcRenderer.invoke("novalist:open-manuscript", chapterId) as Promise<
      OperationResult<DocumentSnapshot>
    >,
  saveManuscript: (input: SaveManuscriptInput) =>
    ipcRenderer.invoke("novalist:save-manuscript", input) as Promise<
      OperationResult<DocumentSnapshot>
    >,
  getReconstructionSnapshot: () =>
    ipcRenderer.invoke("novalist:get-reconstruction-snapshot") as Promise<
      OperationResult<ReconstructionSnapshot>
    >,
  generateReconstruction: () =>
    ipcRenderer.invoke("novalist:generate-reconstruction") as Promise<
      OperationResult<ReconstructionBatch>
    >,
  getReconstructionTaskStatus: () =>
    ipcRenderer.invoke("novalist:get-reconstruction-task-status") as Promise<
      OperationResult<ReconstructionTaskStatus>
    >,
  startReconstruction: (mode, remoteConsent) =>
    ipcRenderer.invoke("novalist:start-reconstruction", mode, remoteConsent) as Promise<
      OperationResult<ReconstructionTask>
    >,
  cancelReconstruction: (taskId) =>
    ipcRenderer.invoke("novalist:cancel-reconstruction", taskId) as Promise<
      OperationResult<ReconstructionTask>
    >,
  getKnowledgeSnapshot: () =>
    ipcRenderer.invoke("novalist:get-knowledge") as Promise<OperationResult<KnowledgeSnapshot>>,
  renameKnowledgeEntity: (entityId, displayName) =>
    ipcRenderer.invoke("novalist:rename-knowledge-entity", entityId, displayName) as Promise<OperationResult<KnowledgeSnapshot>>,
  setKnowledgeEntityAliases: (entityId, aliases) =>
    ipcRenderer.invoke("novalist:set-knowledge-aliases", entityId, aliases) as Promise<OperationResult<KnowledgeSnapshot>>,
  mergeKnowledgeEntities: (sourceEntityId, targetEntityId) =>
    ipcRenderer.invoke("novalist:merge-knowledge-entities", sourceEntityId, targetEntityId) as Promise<OperationResult<KnowledgeSnapshot>>,
  unmergeKnowledgeEntity: (sourceEntityId) =>
    ipcRenderer.invoke("novalist:unmerge-knowledge-entity", sourceEntityId) as Promise<OperationResult<KnowledgeSnapshot>>,
  updateKnowledgeRelation: (relationId, sourceEntityId, targetEntityId, label) =>
    ipcRenderer.invoke("novalist:update-knowledge-relation", relationId, sourceEntityId, targetEntityId, label) as Promise<OperationResult<KnowledgeSnapshot>>,
  deleteKnowledgeRelation: (relationId) =>
    ipcRenderer.invoke("novalist:delete-knowledge-relation", relationId) as Promise<OperationResult<KnowledgeSnapshot>>,
  updateKnowledgeCharacterField: (entityId, field, value) =>
    ipcRenderer.invoke("novalist:update-knowledge-character-field", entityId, field, value) as Promise<OperationResult<KnowledgeSnapshot>>,
  hideKnowledgeCharacterField: (entityId, field) =>
    ipcRenderer.invoke("novalist:hide-knowledge-character-field", entityId, field) as Promise<OperationResult<KnowledgeSnapshot>>,
  restoreKnowledgeCharacterField: (entityId, field) =>
    ipcRenderer.invoke("novalist:restore-knowledge-character-field", entityId, field) as Promise<OperationResult<KnowledgeSnapshot>>,
  updateKnowledgeWorld: (worldId, name, category, description) =>
    ipcRenderer.invoke("novalist:update-knowledge-world", worldId, name, category, description) as Promise<OperationResult<KnowledgeSnapshot>>,
  hideKnowledgeWorld: (worldId) =>
    ipcRenderer.invoke("novalist:hide-knowledge-world", worldId) as Promise<OperationResult<KnowledgeSnapshot>>,
  restoreKnowledgeWorld: (worldId) =>
    ipcRenderer.invoke("novalist:restore-knowledge-world", worldId) as Promise<OperationResult<KnowledgeSnapshot>>,
  updateKnowledgeEvent: (eventId, timeLabel, title, description) =>
    ipcRenderer.invoke("novalist:update-knowledge-event", eventId, timeLabel, title, description) as Promise<OperationResult<KnowledgeSnapshot>>,
  updateKnowledgeEventLinks: (eventId, participantEntityIds, worldIds) =>
    ipcRenderer.invoke("novalist:update-knowledge-event-links", eventId, participantEntityIds, worldIds) as Promise<OperationResult<KnowledgeSnapshot>>,
  reorderKnowledgeEvents: (eventIds) =>
    ipcRenderer.invoke("novalist:reorder-knowledge-events", eventIds) as Promise<OperationResult<KnowledgeSnapshot>>,
  hideKnowledgeEvent: (eventId) =>
    ipcRenderer.invoke("novalist:hide-knowledge-event", eventId) as Promise<OperationResult<KnowledgeSnapshot>>,
  restoreKnowledgeEvent: (eventId) =>
    ipcRenderer.invoke("novalist:restore-knowledge-event", eventId) as Promise<OperationResult<KnowledgeSnapshot>>,
  openKnowledgeCard: (ownerKind, ownerId, mode) =>
    ipcRenderer.invoke("novalist:open-knowledge-card", ownerKind, ownerId, mode) as Promise<OperationResult<KnowledgeCardDocument>>,
  saveKnowledgeAuthorCard: (ownerKind, ownerId, content, expectedRevision) =>
    ipcRenderer.invoke("novalist:save-knowledge-author-card", ownerKind, ownerId, content, expectedRevision) as Promise<OperationResult<KnowledgeCardDocument>>,
  reopenReconstructionProposal: (batchId, proposalId) =>
    ipcRenderer.invoke("novalist:reopen-reconstruction-proposal", batchId, proposalId) as Promise<OperationResult<ReconstructionBatch>>,
  getReconstructionBatch: (batchId) =>
    ipcRenderer.invoke("novalist:get-reconstruction-batch", batchId) as Promise<
      OperationResult<ReconstructionBatch>
    >,
  reviewReconstruction: (batchId, proposalId, decision) =>
    ipcRenderer.invoke("novalist:review-reconstruction", batchId, proposalId, decision) as Promise<
      OperationResult<ReconstructionBatch>
    >,
  reviewReconstructionMany: (batchId, decisions) =>
    ipcRenderer.invoke("novalist:review-reconstruction-many", batchId, decisions) as Promise<
      OperationResult<ReconstructionBatch>
    >,
  restoreLastProject: () =>
    ipcRenderer.invoke("novalist:restore-last-project") as Promise<
      OperationResult<OpenedProject | null>
    >,
  closeProject: () =>
    ipcRenderer.invoke("novalist:close-project") as Promise<
      OperationResult<{ closed: boolean }>
    >,
  getPreferences: () =>
    ipcRenderer.invoke("novalist:get-preferences") as Promise<
      OperationResult<PreferencesSnapshot>
    >,
  updatePreferences: (patch: PreferencesPatch) =>
    ipcRenderer.invoke("novalist:update-preferences", patch) as Promise<
      OperationResult<PreferencesSnapshot>
    >,
  getProjectSnapshot: () =>
    ipcRenderer.invoke("novalist:get-project-snapshot") as Promise<
      OperationResult<ProjectSnapshot>
    >,
  getRelationshipGraph: () =>
    ipcRenderer.invoke("novalist:get-relationship-graph") as Promise<
      OperationResult<RelationshipGraphSnapshot>
    >,
  chooseAndOpenDocument: (category) =>
    ipcRenderer.invoke("novalist:choose-document", category) as Promise<
      OperationResult<DocumentSnapshot | null>
    >,
  openDocument: (path, category) =>
    ipcRenderer.invoke("novalist:open-document", path, category) as Promise<
      OperationResult<DocumentSnapshot>
    >,
  saveDocument: (input: SaveDocumentInput) =>
    ipcRenderer.invoke("novalist:save-document", input) as Promise<
      OperationResult<DocumentSnapshot>
    >,
  createChapter: (title, chapterId) =>
    ipcRenderer.invoke("novalist:create-chapter", title, chapterId) as Promise<
      OperationResult<MutationResult>
    >,
  createCanonEntry: (kind, title) =>
    ipcRenderer.invoke("novalist:create-canon-entry", kind, title) as Promise<
      OperationResult<MutationResult>
    >,
  createTimeline: () =>
    ipcRenderer.invoke("novalist:create-timeline") as Promise<
      OperationResult<MutationResult>
    >,
  importMarkdown: () =>
    ipcRenderer.invoke("novalist:import-markdown") as Promise<
      OperationResult<ImportResult | null>
    >,
  deleteDocument: (item: ProjectDocumentItem) =>
    ipcRenderer.invoke("novalist:delete-document", item) as Promise<
      OperationResult<MutationResult | null>
    >,
  setSystemImportance: (path, importance) =>
    ipcRenderer.invoke("novalist:set-system-importance", path, importance) as Promise<
      OperationResult<ProjectSnapshot>
    >,
  getTrash: () => ipcRenderer.invoke("novalist:get-trash") as Promise<OperationResult<TrashSnapshot>>,
  restoreTrash: (kind, trashId, conflictPolicy = "error") =>
    ipcRenderer.invoke("novalist:restore-trash", kind, trashId, conflictPolicy) as Promise<
      OperationResult<{ restoredPath: string; snapshot: ProjectSnapshot; trash: TrashSnapshot }>
    >,
  deleteTrashForever: (item: TrashItem) =>
    ipcRenderer.invoke("novalist:delete-trash-forever", item) as Promise<
      OperationResult<TrashSnapshot | null>
    >,
  getAIStatus: () => ipcRenderer.invoke("novalist:ai-status") as Promise<OperationResult<AIStatus>>,
  startAITask: (input: AIStartInput) =>
    ipcRenderer.invoke("novalist:ai-start", input) as Promise<OperationResult<AITask>>,
  cancelAITask: (taskId) =>
    ipcRenderer.invoke("novalist:ai-cancel", taskId) as Promise<OperationResult<AITask>>,
  getAIResult: (taskId) =>
    ipcRenderer.invoke("novalist:ai-result", taskId) as Promise<OperationResult<AIResultEnvelope>>,
  discardAIResult: (taskId) =>
    ipcRenderer.invoke("novalist:ai-discard", taskId) as Promise<OperationResult<AITask>>,
  applyAIWritingResult: (taskId) =>
    ipcRenderer.invoke("novalist:ai-apply-writing", taskId) as Promise<
      OperationResult<DocumentSnapshot | null>
    >,
  commitAIMemoryResult: (taskId) =>
    ipcRenderer.invoke("novalist:ai-commit-memory", taskId) as Promise<
      OperationResult<Record<string, unknown> | null>
    >,
  confirmDiscardChanges: (actionLabel) =>
    ipcRenderer.invoke("novalist:confirm-discard", actionLabel) as Promise<boolean>,
  setDocumentDirty: (dirty) => ipcRenderer.send("novalist:document-dirty", dirty),
  onAppEvent: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, value: AppEvent) => listener(value);
    ipcRenderer.on("novalist:app-event", handler);
    return () => ipcRenderer.removeListener("novalist:app-event", handler);
  },
};

contextBridge.exposeInMainWorld("novalist", Object.freeze(bridge));

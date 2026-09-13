export interface RpcError {
  code: string;
  message: string;
  retryable: boolean;
  data?: Record<string, unknown>;
}

export type OperationResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: RpcError };

export interface SchemaRange {
  minimum: number;
  maximum: number;
}

export interface Handshake {
  applicationVersion: string;
  protocolVersion: number;
  sidecarBuildVersion: string;
  supportedMethods: string[];
  projectSchema: SchemaRange;
  configSchema: SchemaRange;
  client: { name: string; version: string };
}

export interface ProjectDescriptor {
  root: string;
  name: string;
  author: string;
}

export interface MigrationDescriptor {
  fromSchema: number;
  toSchema: number;
  changedFiles: string[];
  backupPath: string | null;
  recoveredInterruptedMigration: boolean;
}

export interface OpenedProject {
  project: ProjectDescriptor;
  migration: MigrationDescriptor;
}

export interface OpenedProjectV2 {
  root: string;
  projectId: string;
  name: string;
  author: string;
  schemaVersion: 2;
}

export interface ImportChapterPreview {
  chapterId: string;
  sequence: number;
  title: string;
  sourceName: string;
  encoding: string;
  byteCount: number;
  contentSha256: string;
  excerpt: string;
  empty: boolean;
}

export interface ImportWarning {
  code: string;
  message: string;
  source: string;
}

export interface ManuscriptImportPlan {
  sourceLabel: string;
  sourceKind: "novalist_v1_manuscript" | "external_manuscript";
  digest: string;
  totalSourceBytes: number;
  chapters: ImportChapterPreview[];
  warnings: ImportWarning[];
}

export interface ManuscriptItem {
  chapterId: string;
  sequence: number;
  title: string;
  path: string;
  relativePath: string;
}

export interface ManuscriptSnapshot {
  chapters: ManuscriptItem[];
  itemCount: number;
}

export interface SaveManuscriptInput {
  chapterId: string;
  content: string;
  expectedRevision: string | null;
  force?: boolean;
}

export interface ReconstructionEvidence {
  evidenceId: string;
  chapterId: string;
  chapterRevision: string;
  start: number;
  end: number;
  anchor: string;
  text: string;
}

export interface ReconstructionProposal {
  proposalId: string;
  kind: "entity" | "relation" | "world" | "character_field" | "event";
  status: "pending" | "accepted" | "rejected";
  confidence: number;
  producers: Array<"local" | "dsh">;
  reviewMode: "" | "single" | "batch";
  reviewedAt: string;
  evidence: ReconstructionEvidence[];
  name?: string;
  entityType?: "character";
  sourceName?: string;
  targetName?: string;
  label?: string;
  category?: string;
  description?: string;
  characterName?: string;
  field?: string;
  value?: string;
  timeLabel?: string;
  title?: string;
  characterNames?: string[];
  worldNames?: string[];
}

export interface ReconstructionBatchSummary {
  batchId: string;
  sourceRevision: string;
  createdAt: string;
  status: "pending" | "reviewed" | "stale";
  proposalCount: number;
  pendingCount: number;
  acceptedCount: number;
  requestedMode: "local" | "dsh";
  producer: "local" | "local+dsh";
  fallbackUsed: boolean;
}

export interface ReconstructionExtractionSummary {
  requestedMode: "local" | "dsh";
  producer: "local" | "local+dsh";
  fallbackUsed: boolean;
  fallbackReason: string;
  segmentCount: number;
  remoteChunkCount: number;
  estimatedInputTokens: number;
  privacyScope: "local_only" | "manuscript_evidence_segments_only";
  duplicateCount: number;
  relationConflictCount: number;
}

export interface ReconstructionBatch {
  batchId: string;
  sourceRevision: string;
  createdAt: string;
  status: "pending" | "reviewed" | "stale";
  chapterRevisions: Record<string, string>;
  segmentCount: number;
  extraction: ReconstructionExtractionSummary;
  proposals: ReconstructionProposal[];
}

export interface ReconstructionSnapshot {
  sourceRevision: string;
  acceptedEntityCount: number;
  acceptedRelationCount: number;
  acceptedWorldCount: number;
  acceptedCharacterFieldCount: number;
  acceptedEventCount: number;
  pendingCount: number;
  staleBatchCount: number;
  batches: ReconstructionBatchSummary[];
}

export interface ReconstructionTask {
  taskId: string;
  projectRoot: string;
  sourceRevision: string;
  requestedMode: "local" | "dsh";
  status: "queued" | "running" | "cancel_requested" | "succeeded" | "failed" | "cancelled";
  stage: string;
  progress: number;
  startedAt: string;
  finishedAt: string | null;
  batchId: string;
  error: string;
}

export interface ReconstructionTaskStatus {
  active: ReconstructionTask | null;
  recent: ReconstructionTask | null;
}

export interface KnowledgeEntity {
  entityId: string;
  entityType: "character";
  displayName: string;
  aliases: string[];
  profileFields: Record<string, string>;
  hiddenProfileFields: Record<string, string>;
  profileEvidenceIds: Record<string, string[]>;
  evidenceIds: string[];
  reviewedAt: string;
  cardRelativePath: string;
}

export interface KnowledgeRelation {
  relationId: string;
  sourceEntityId: string;
  targetEntityId: string;
  label: string;
  evidenceIds: string[];
  reviewedAt: string;
}

export interface KnowledgeWorld {
  worldId: string;
  name: string;
  category: string;
  description: string;
  evidenceIds: string[];
  reviewedAt: string;
  cardRelativePath: string;
}

export interface KnowledgeMerge {
  sourceEntityId: string;
  targetEntityId: string;
  sourceName: string;
  targetName: string;
}

export interface KnowledgeEvent {
  eventId: string;
  timeLabel: string;
  title: string;
  description: string;
  participantEntityIds: string[];
  worldIds: string[];
  evidenceIds: string[];
  reviewedAt: string;
  order: number;
}

export interface KnowledgeDiagnostic {
  diagnosticId: string;
  code: "temporal_overlap" | "missing_character_link" | "inactive_world_link";
  severity: "notice" | "warning";
  message: string;
  eventIds: string[];
  evidenceIds: string[];
}

export interface KnowledgeSnapshot {
  entities: KnowledgeEntity[];
  relations: KnowledgeRelation[];
  worlds: KnowledgeWorld[];
  hiddenWorlds: KnowledgeWorld[];
  events: KnowledgeEvent[];
  hiddenEvents: KnowledgeEvent[];
  evidence: ReconstructionEvidence[];
  diagnostics: KnowledgeDiagnostic[];
  merges: KnowledgeMerge[];
  operationCount: number;
}

export interface KnowledgeCardDocument {
  ownerKind: "character" | "world";
  ownerId: string;
  mode: "generated" | "author";
  title: string;
  relativePath: string;
  content: string;
  revision: string;
  readOnly: boolean;
}

export interface DocumentSnapshot {
  path: string;
  relativePath: string;
  category: string;
  title: string;
  content: string;
  revision: string;
}

export type DocumentKind =
  | "chapter"
  | "outline"
  | "plan"
  | "style"
  | "character"
  | "world"
  | "power"
  | "timeline";

export interface ProjectDocumentItem {
  id: string;
  kind: DocumentKind;
  category: string;
  title: string;
  path: string;
  relativePath: string;
  protected: boolean;
  importance: "core" | "non_core" | null;
}

export interface ProjectSnapshot {
  chapters: ProjectDocumentItem[];
  outlines: ProjectDocumentItem[];
  characters: ProjectDocumentItem[];
  world: ProjectDocumentItem[];
  power: ProjectDocumentItem[];
  timeline: ProjectDocumentItem[];
  nextChapterId: string;
  itemCount: number;
}

export interface PreferencesSnapshot {
  theme: "light" | "dark";
  uiFontSize: number;
  editorFontSize: number;
  autoSave: boolean;
  autoSaveInterval: number;
  showLineNumbers: boolean;
  lastProject: string;
  recentProjects: string[];
}

export type PreferencesPatch = Partial<Pick<
  PreferencesSnapshot,
  | "theme"
  | "uiFontSize"
  | "editorFontSize"
  | "autoSave"
  | "autoSaveInterval"
  | "showLineNumbers"
>>;

export interface DocumentMutation {
  resultPath: string;
  changedPaths: string[];
  kind: string;
}

export interface MutationResult {
  mutation: DocumentMutation;
  snapshot: ProjectSnapshot;
}

export interface ImportResult {
  imported: DocumentMutation[];
  snapshot: ProjectSnapshot;
}

export interface TrashItem {
  trashId: string;
  kind: "chapter" | "character" | "world" | "power" | "timeline";
  title: string;
  originalPath: string;
  deletedAt: string;
  canRename: boolean;
}

export interface TrashSnapshot {
  items: TrashItem[];
}

export interface GraphEvidence {
  chapterId: string;
  anchor: string;
  description: string;
  certainty: string;
}

export interface GraphNode {
  id: string;
  name: string;
  aliases: string[];
  state: string;
  location: string;
  resolved: boolean;
  path: string | null;
  relativePath: string | null;
  nodeKind: "character" | "world" | "event";
  order: number | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  directed: true;
  sourceKind: "story_state" | "character_card" | "reviewed_v2";
  edgeKind: "relationship" | "participation" | "setting";
  evidence: GraphEvidence[];
}

export interface GraphWarning {
  code: string;
  message: string;
  subject: string;
  target: string;
}

export interface RelationshipGraphSnapshot {
  projectName: string;
  revision: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  warnings: GraphWarning[];
  relationTypes: string[];
}

export type AITaskKind = "expand" | "continuation" | "check" | "memory" | "connection";
export type AITaskState =
  | "queued"
  | "running"
  | "cancel_requested"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "applied"
  | "discarded";

export interface AITask {
  taskId: string;
  kind: AITaskKind;
  chapterId: string;
  status: AITaskState;
  stage: string;
  progress: number;
  startedAt: string;
  finishedAt: string | null;
  error: string;
  hasResult: boolean;
}

export interface AIStatus {
  active: AITask | null;
  recent: AITask[];
  supportedKinds: AITaskKind[];
}

export interface AIWritingResult {
  type: "writing";
  mode: "replace" | "append";
  text: string;
  charCount: number;
  targetChars: number;
  minChars: number;
  maxChars: number;
  reviewMinChars: number;
  reviewMaxChars: number;
  lengthStatus: string;
  warning: string;
}

export interface AIConsistencyResult {
  type: "consistency";
  report: Record<string, unknown>;
  formatted: string;
}

export interface AIMemoryResult {
  type: "memory";
  summary: string;
  preview: string;
  hasBlockers: boolean;
  conflictCount: number;
  patchCount: number;
  cacheHit: boolean;
}

export interface AIConnectionResult {
  type: "connection";
  message: string;
}

export type AIResult = AIWritingResult | AIConsistencyResult | AIMemoryResult | AIConnectionResult;
export interface AIResultEnvelope { task: AITask; result: AIResult }
export interface AIStartInput {
  kind: AITaskKind;
  chapterId: string;
  sourceRevision: string | null;
  noticeAccepted: boolean;
  options?: { selectedPower?: string[]; selectedForeshadowing?: Array<Record<string, unknown>> };
}

export interface SaveDocumentInput {
  path: string;
  category: string;
  content: string;
  expectedRevision: string | null;
  force?: boolean;
}

export interface PreviewStatus {
  connected: boolean;
  handshake: Handshake;
}

export type AppEvent =
  | { event: "sidecar.ready"; data: { protocolVersion: number } }
  | { event: "sidecar.stopping"; data: Record<string, never> }
  | { event: "project.opened"; data: { opened: OpenedProject } }
  | { event: "projectV2.opened"; data: { opened: OpenedProjectV2 } }
  | { event: "project.closed"; data: { root: string | null } }
  | {
      event: "document.changed";
      data: {
        path: string;
        relativePath: string;
        kind: string;
        revision: string;
      };
    }
  | {
      event: "project.contentChanged";
      data: { kind: string; changedPaths: string[] };
    }
  | { event: "trash.changed"; data: { kind: string; trashId: string } }
  | { event: "ai.taskUpdated"; data: { task: AITask } }
  | { event: "ai.contextReport"; data: { taskId: string; report: Record<string, unknown> } }
  | { event: "manuscript.changed"; data: { chapterId: string; relativePath: string; revision: string; reconstructionInvalidated: boolean } }
  | { event: "reconstruction.updated"; data: { batchId: string } }
  | { event: "reconstruction.taskUpdated"; data: { task: ReconstructionTask } }
  | { event: "knowledge.updated"; data: { kind: string } };

export interface NovalistBridge {
  getStatus(): Promise<OperationResult<PreviewStatus>>;
  chooseAndOpenProject(): Promise<OperationResult<OpenedProject | null>>;
  createProject(
    name: string,
    author: string,
  ): Promise<OperationResult<OpenedProject | null>>;
  chooseAndScanManuscript(
    sourceType: "file" | "directory",
  ): Promise<OperationResult<ManuscriptImportPlan | null>>;
  createProjectV2(
    name: string,
    author: string,
    planDigest: string,
  ): Promise<OperationResult<OpenedProjectV2 | null>>;
  chooseAndOpenProjectV2(): Promise<OperationResult<OpenedProjectV2 | null>>;
  getManuscriptSnapshot(): Promise<OperationResult<ManuscriptSnapshot>>;
  openManuscript(chapterId: string): Promise<OperationResult<DocumentSnapshot>>;
  saveManuscript(input: SaveManuscriptInput): Promise<OperationResult<DocumentSnapshot>>;
  getReconstructionSnapshot(): Promise<OperationResult<ReconstructionSnapshot>>;
  generateReconstruction(): Promise<OperationResult<ReconstructionBatch>>;
  getReconstructionTaskStatus(): Promise<OperationResult<ReconstructionTaskStatus>>;
  startReconstruction(mode: "local" | "dsh", remoteConsent: boolean): Promise<OperationResult<ReconstructionTask>>;
  cancelReconstruction(taskId: string): Promise<OperationResult<ReconstructionTask>>;
  getKnowledgeSnapshot(): Promise<OperationResult<KnowledgeSnapshot>>;
  renameKnowledgeEntity(entityId: string, displayName: string): Promise<OperationResult<KnowledgeSnapshot>>;
  setKnowledgeEntityAliases(entityId: string, aliases: string[]): Promise<OperationResult<KnowledgeSnapshot>>;
  mergeKnowledgeEntities(sourceEntityId: string, targetEntityId: string): Promise<OperationResult<KnowledgeSnapshot>>;
  unmergeKnowledgeEntity(sourceEntityId: string): Promise<OperationResult<KnowledgeSnapshot>>;
  updateKnowledgeRelation(
    relationId: string,
    sourceEntityId: string,
    targetEntityId: string,
    label: string,
  ): Promise<OperationResult<KnowledgeSnapshot>>;
  deleteKnowledgeRelation(relationId: string): Promise<OperationResult<KnowledgeSnapshot>>;
  updateKnowledgeCharacterField(entityId: string, field: string, value: string): Promise<OperationResult<KnowledgeSnapshot>>;
  hideKnowledgeCharacterField(entityId: string, field: string): Promise<OperationResult<KnowledgeSnapshot>>;
  restoreKnowledgeCharacterField(entityId: string, field: string): Promise<OperationResult<KnowledgeSnapshot>>;
  updateKnowledgeWorld(worldId: string, name: string, category: string, description: string): Promise<OperationResult<KnowledgeSnapshot>>;
  hideKnowledgeWorld(worldId: string): Promise<OperationResult<KnowledgeSnapshot>>;
  restoreKnowledgeWorld(worldId: string): Promise<OperationResult<KnowledgeSnapshot>>;
  updateKnowledgeEvent(eventId: string, timeLabel: string, title: string, description: string): Promise<OperationResult<KnowledgeSnapshot>>;
  updateKnowledgeEventLinks(eventId: string, participantEntityIds: string[], worldIds: string[]): Promise<OperationResult<KnowledgeSnapshot>>;
  reorderKnowledgeEvents(eventIds: string[]): Promise<OperationResult<KnowledgeSnapshot>>;
  hideKnowledgeEvent(eventId: string): Promise<OperationResult<KnowledgeSnapshot>>;
  restoreKnowledgeEvent(eventId: string): Promise<OperationResult<KnowledgeSnapshot>>;
  openKnowledgeCard(ownerKind: "character" | "world", ownerId: string, mode: "generated" | "author"): Promise<OperationResult<KnowledgeCardDocument>>;
  saveKnowledgeAuthorCard(ownerKind: "character" | "world", ownerId: string, content: string, expectedRevision: string): Promise<OperationResult<KnowledgeCardDocument>>;
  reopenReconstructionProposal(batchId: string, proposalId: string): Promise<OperationResult<ReconstructionBatch>>;
  getReconstructionBatch(batchId: string): Promise<OperationResult<ReconstructionBatch>>;
  reviewReconstruction(
    batchId: string,
    proposalId: string,
    decision: "accepted" | "rejected",
  ): Promise<OperationResult<ReconstructionBatch>>;
  reviewReconstructionMany(
    batchId: string,
    decisions: Array<{ proposalId: string; decision: "accepted" | "rejected" }>,
  ): Promise<OperationResult<ReconstructionBatch>>;
  restoreLastProject(): Promise<OperationResult<OpenedProject | null>>;
  closeProject(): Promise<OperationResult<{ closed: boolean }>>;
  getPreferences(): Promise<OperationResult<PreferencesSnapshot>>;
  updatePreferences(
    patch: PreferencesPatch,
  ): Promise<OperationResult<PreferencesSnapshot>>;
  getProjectSnapshot(): Promise<OperationResult<ProjectSnapshot>>;
  getRelationshipGraph(): Promise<OperationResult<RelationshipGraphSnapshot>>;
  chooseAndOpenDocument(
    category: string,
  ): Promise<OperationResult<DocumentSnapshot | null>>;
  openDocument(
    path: string,
    category: string,
  ): Promise<OperationResult<DocumentSnapshot>>;
  saveDocument(
    input: SaveDocumentInput,
  ): Promise<OperationResult<DocumentSnapshot>>;
  createChapter(
    title: string,
    chapterId: string,
  ): Promise<OperationResult<MutationResult>>;
  createCanonEntry(
    kind: "character" | "world" | "power",
    title: string,
  ): Promise<OperationResult<MutationResult>>;
  createTimeline(): Promise<OperationResult<MutationResult>>;
  importMarkdown(): Promise<OperationResult<ImportResult | null>>;
  deleteDocument(
    item: ProjectDocumentItem,
  ): Promise<OperationResult<MutationResult | null>>;
  setSystemImportance(
    path: string,
    importance: "core" | "non_core",
  ): Promise<OperationResult<ProjectSnapshot>>;
  getTrash(): Promise<OperationResult<TrashSnapshot>>;
  restoreTrash(
    kind: TrashItem["kind"],
    trashId: string,
    conflictPolicy?: "error" | "rename",
  ): Promise<OperationResult<{ restoredPath: string; snapshot: ProjectSnapshot; trash: TrashSnapshot }>>;
  deleteTrashForever(
    item: TrashItem,
  ): Promise<OperationResult<TrashSnapshot | null>>;
  getAIStatus(): Promise<OperationResult<AIStatus>>;
  startAITask(input: AIStartInput): Promise<OperationResult<AITask>>;
  cancelAITask(taskId: string): Promise<OperationResult<AITask>>;
  getAIResult(taskId: string): Promise<OperationResult<AIResultEnvelope>>;
  discardAIResult(taskId: string): Promise<OperationResult<AITask>>;
  applyAIWritingResult(taskId: string): Promise<OperationResult<DocumentSnapshot | null>>;
  commitAIMemoryResult(taskId: string): Promise<OperationResult<Record<string, unknown> | null>>;
  confirmDiscardChanges(actionLabel: string): Promise<boolean>;
  setDocumentDirty(dirty: boolean): void;
  onAppEvent(listener: (event: AppEvent) => void): () => void;
}

declare global {
  interface Window {
    novalist: NovalistBridge;
  }
}

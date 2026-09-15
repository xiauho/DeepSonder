import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  ReconstructionBatch,
  ReconstructionProposal,
  ReconstructionSnapshot,
  ReconstructionTask,
  RpcError,
} from "../../../shared/contracts";

type ReviewFilter = "pending" | "conflicts" | "duplicates" | "reviewed" | "all";

interface ReconstructionDrawerProps {
  refreshToken: number;
  onClose: () => void;
  onError: (error: RpcError) => void;
  onKnowledgeChanged: () => void;
  onOpenEvidence: (chapterId: string) => void;
  documentDirty: boolean;
}

export function ReconstructionDrawer({
  refreshToken,
  onClose,
  onError,
  onKnowledgeChanged,
  onOpenEvidence,
  documentDirty,
}: ReconstructionDrawerProps) {
  const [snapshot, setSnapshot] = useState<ReconstructionSnapshot | null>(null);
  const [batch, setBatch] = useState<ReconstructionBatch | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<ReviewFilter>("pending");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [acceptedEntityNames, setAcceptedEntityNames] = useState<Set<string>>(new Set());
  const [acceptedWorldNames, setAcceptedWorldNames] = useState<Set<string>>(new Set());
  const [task, setTask] = useState<ReconstructionTask | null>(null);
  const [remoteConsent, setRemoteConsent] = useState(false);

  const load = useCallback(async () => {
    const result = await window.novalist.getReconstructionSnapshot();
    if (!result.ok) { onError(result.error); return; }
    setSnapshot(result.value);
    const knowledge = await window.novalist.getKnowledgeSnapshot();
    if (knowledge.ok) {
      setAcceptedEntityNames(new Set(knowledge.value.entities.map((item) => item.displayName)));
      setAcceptedWorldNames(new Set(knowledge.value.worlds.map((item) => item.name)));
    }
    else onError(knowledge.error);
    const taskStatus = await window.novalist.getReconstructionTaskStatus();
    if (taskStatus.ok) setTask(taskStatus.value.active ?? taskStatus.value.recent);
    const latest = [...result.value.batches].reverse().find((item) => item.status !== "stale");
    if (latest === undefined) { setBatch(null); return; }
    const loaded = await window.novalist.getReconstructionBatch(latest.batchId);
    if (loaded.ok) setBatch(loaded.value); else onError(loaded.error);
  }, [onError]);

  useEffect(() => { void load(); }, [load, refreshToken]);
  useEffect(() => window.novalist.onAppEvent((event) => {
    if (event.event !== "reconstruction.taskUpdated") return;
    setTask(event.data.task);
    if (event.data.task.status === "succeeded") {
      void load();
      onKnowledgeChanged();
    }
  }), [load, onKnowledgeChanged]);
  useEffect(() => {
    if (task === null || !["queued", "running", "cancel_requested"].includes(task.status)) return;
    let disposed = false;
    const timer = window.setInterval(() => {
      void window.novalist.getReconstructionTaskStatus().then((result) => {
        if (disposed || !result.ok) return;
        const current = result.value.active ?? result.value.recent;
        if (current === null) return;
        setTask(current);
        if (current.status === "succeeded") {
          window.clearInterval(timer);
          void load();
          onKnowledgeChanged();
        }
      });
    }, 250);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [load, onKnowledgeChanged, task?.status, task?.taskId]);

  const generate = async () => {
    setBusy(true);
    const requestedRemote = remoteConsent;
    const result = await window.novalist.startReconstruction(requestedRemote ? "dsh" : "local", requestedRemote);
    setBusy(false);
    if (!result.ok) { onError(result.error); return; }
    setRemoteConsent(false);
    setTask(result.value);
  };

  const cancel = async () => {
    if (task === null) return;
    const result = await window.novalist.cancelReconstruction(task.taskId);
    if (result.ok) setTask(result.value); else onError(result.error);
  };

  const review = async (proposal: ReconstructionProposal, decision: "accepted" | "rejected") => {
    if (batch === null) return;
    setBusy(true);
    const result = await window.novalist.reviewReconstruction(batch.batchId, proposal.proposalId, decision);
    setBusy(false);
    if (!result.ok) { onError(result.error); return; }
    setSelected((value) => { const next = new Set(value); next.delete(proposal.proposalId); return next; });
    setBatch(result.value);
    await load();
    onKnowledgeChanged();
  };

  const reviewSelected = async (decision: "accepted" | "rejected") => {
    if (batch === null) return;
    const decisions = batch.proposals.filter((item) => item.status === "pending" && selected.has(item.proposalId))
      .map((item) => ({ proposalId: item.proposalId, decision }));
    if (decisions.length === 0) return;
    const label = decision === "accepted" ? "接受" : "排除";
    if (!window.confirm(`确认${label}已勾选的 ${decisions.length} 个候选？系统会先整体校验，失败时不会写入任何一项。`)) return;
    setBusy(true);
    const result = await window.novalist.reviewReconstructionMany(batch.batchId, decisions);
    setBusy(false);
    if (!result.ok) { onError(result.error); return; }
    setSelected(new Set());
    setBatch(result.value);
    await load();
    onKnowledgeChanged();
  };

  const reopen = async (proposal: ReconstructionProposal) => {
    if (batch === null) return;
    setBusy(true);
    const result = await window.novalist.reopenReconstructionProposal(batch.batchId, proposal.proposalId);
    setBusy(false);
    if (!result.ok) { onError(result.error); return; }
    setSelected((value) => { const next = new Set(value); next.delete(proposal.proposalId); return next; });
    setBatch(result.value);
    await load();
    onKnowledgeChanged();
  };

  const conflictIds = useMemo(() => {
    const groups = new Map<string, ReconstructionProposal[]>();
    for (const proposal of batch?.proposals ?? []) {
      let key = "";
      if (proposal.kind === "relation") key = `relation\0${[proposal.sourceName ?? "", proposal.targetName ?? ""].map((item) => item.toLocaleLowerCase("zh-CN")).sort().join("\0")}`;
      else if (proposal.kind === "character_field") key = `field\0${proposal.characterName?.toLocaleLowerCase("zh-CN")}\0${proposal.field}`;
      else if (proposal.kind === "world") key = `world\0${proposal.name?.toLocaleLowerCase("zh-CN")}\0${proposal.category?.toLocaleLowerCase("zh-CN")}`;
      else if (proposal.kind === "event") key = `event\0${proposal.timeLabel?.toLocaleLowerCase("zh-CN")}\0${proposal.title?.toLocaleLowerCase("zh-CN")}`;
      else continue;
      groups.set(key, [...(groups.get(key) ?? []), proposal]);
    }
    return new Set([...groups.values()].filter((items) => new Set(items.map(proposalVariant)).size > 1).flatMap((items) => items.map((item) => item.proposalId)));
  }, [batch]);
  const proposals = useMemo(() => (batch?.proposals ?? []).filter((item) => {
    if (filter === "pending") return item.status === "pending";
    if (filter === "conflicts") return item.status === "pending" && conflictIds.has(item.proposalId);
    if (filter === "duplicates") return item.status === "pending" && item.producers.length > 1;
    if (filter === "reviewed") return item.status !== "pending";
    return true;
  }).sort((left, right) => Number(conflictIds.has(right.proposalId)) - Number(conflictIds.has(left.proposalId)) ||
    Number(right.producers.length > 1) - Number(left.producers.length > 1) || right.confidence - left.confidence), [batch, conflictIds, filter]);
  const visiblePendingIds = proposals.filter((item) => item.status === "pending").map((item) => item.proposalId);
  const selectedPending = batch?.proposals.filter((item) => item.status === "pending" && selected.has(item.proposalId)) ?? [];
  const acceptedOrSelectedNames = new Set([...acceptedEntityNames, ...(batch?.proposals.filter((item) => item.kind === "entity" && (item.status === "accepted" || selected.has(item.proposalId))).map((item) => item.name) ?? [])]);
  const acceptedOrSelectedWorlds = new Set([...acceptedWorldNames, ...(batch?.proposals.filter((item) => item.kind === "world" && (item.status === "accepted" || selected.has(item.proposalId))).map((item) => item.name) ?? [])]);
  const missingDependencies = new Set<string>();
  for (const item of selectedPending) {
    const characterNames = item.kind === "relation" ? [item.sourceName, item.targetName] : item.kind === "character_field" ? [item.characterName] : item.kind === "event" ? item.characterNames ?? [] : [];
    for (const name of characterNames) if (name !== undefined && !acceptedOrSelectedNames.has(name)) missingDependencies.add(name);
    if (item.kind === "event") for (const name of item.worldNames ?? []) if (!acceptedOrSelectedWorlds.has(name)) missingDependencies.add(name);
  }
  const taskRunning = task !== null && ["queued", "running", "cancel_requested"].includes(task.status);

  return <aside className="reconstruction-drawer" aria-label="正文识别审核">
    <header><div><span className="eyebrow">EVIDENCE REVIEW</span><h2>人物、世界与事件识别</h2></div><button className="icon-button" onClick={onClose}>×</button></header>
    <p className="reconstruction-scope">本地规则和可选 DSH 增强都只生成候选。只有你明确接受的内容才会进入知识库与关系图谱；修改正文会使对应审核结果失效。</p>
    <div className="reconstruction-stats"><span><strong>{snapshot?.acceptedEntityCount ?? 0}</strong>人物</span><span><strong>{snapshot?.acceptedRelationCount ?? 0}</strong>关系</span><span><strong>{snapshot?.acceptedWorldCount ?? 0}</strong>世界观</span><span><strong>{snapshot?.acceptedCharacterFieldCount ?? 0}</strong>角色字段</span><span><strong>{snapshot?.acceptedEventCount ?? 0}</strong>事件</span><span><strong>{snapshot?.pendingCount ?? 0}</strong>待审核</span></div>
    <div className="reconstruction-mode"><label><input type="checkbox" checked={remoteConsent} onChange={(event) => setRemoteConsent(event.target.checked)} disabled={taskRunning} /><span><strong>使用 DSH 增强识别</strong><small>勾选即授权本次发送必要的正文证据片段；不发送项目路径、旧人物卡、世界观或人工整理数据。失败时自动回退本地规则。</small></span></label></div>
    <div className="reconstruction-actions"><button className="primary-button" onClick={() => void generate()} disabled={busy || taskRunning || documentDirty}>{busy ? "正在启动…" : taskRunning ? `正在${task.requestedMode === "dsh" ? " DSH 增强" : "本地"}识别…` : documentDirty ? "请先保存正文" : batch === null ? "扫描正文生成候选" : "重新读取当前正文"}</button><span className="confidence-note">置信度仅供排序，不会自动采用</span></div>
    {taskRunning && <section className="reconstruction-progress"><div><strong>{task.stage}</strong><span>{task.progress}%</span></div><i><b style={{ width: `${task.progress}%` }} /></i><button onClick={() => void cancel()} disabled={task.status === "cancel_requested"}>{task.status === "cancel_requested" ? "正在取消…" : "取消识别"}</button></section>}
    {task?.status === "failed" && <p className="reconstruction-failure">识别失败：{task.error}</p>}
    {batch?.extraction.fallbackUsed && <p className="reconstruction-fallback">DSH 结果不可用或未通过结构校验，本批次已安全回退到本地规则；没有远程结果进入候选。</p>}
    {(snapshot?.staleBatchCount ?? 0) > 0 && <p className="stale-notice">有 {snapshot!.staleBatchCount} 个历史批次因正文变化而失效，已从知识与图谱中排除。</p>}
    {batch === null ? <div className="reconstruction-empty">尚未扫描正文。识别过程完全在本机完成。</div> : <div className="proposal-list">
      <div className="proposal-batch-meta"><span>{batch.segmentCount} 个证据片段</span><span>{batch.proposals.length} 个候选</span><span>{batch.extraction.producer === "local+dsh" ? `本地 + DSH · ${batch.extraction.remoteChunkCount} 批` : "本地规则"}</span>{batch.extraction.duplicateCount > 0 && <span>{batch.extraction.duplicateCount} 项重复已合并</span>}{batch.extraction.relationConflictCount > 0 && <span className="conflict-count">{batch.extraction.relationConflictCount} 项关系冲突待审核</span>}</div>
      <nav className="review-filters" aria-label="候选筛选">{([['pending', '待审核'], ['conflicts', `冲突 ${conflictIds.size}`], ['duplicates', '重复'], ['reviewed', '已审核'], ['all', '全部']] as Array<[ReviewFilter, string]>).map(([value, label]) => <button className={filter === value ? "active" : ""} key={value} onClick={() => setFilter(value)}>{label}</button>)}</nav>
      <div className="bulk-review"><label><input type="checkbox" checked={visiblePendingIds.length > 0 && visiblePendingIds.every((id) => selected.has(id))} onChange={(event) => setSelected((current) => { const next = new Set(current); for (const id of visiblePendingIds) event.target.checked ? next.add(id) : next.delete(id); return next; })} disabled={visiblePendingIds.length === 0 || busy || taskRunning} />选择当前 {visiblePendingIds.length} 项</label><span>{selectedPending.length} 项已选</span><button onClick={() => void reviewSelected("rejected")} disabled={selectedPending.length === 0 || busy || taskRunning}>批量排除</button><button className="accept" onClick={() => void reviewSelected("accepted")} disabled={selectedPending.length === 0 || missingDependencies.size > 0 || busy || taskRunning}>批量接受</button></div>
      {missingDependencies.size > 0 && <p className="dependency-notice">批量接受前还需勾选或先接受相关人物/世界观：{[...missingDependencies].join("、")}</p>}
      {proposals.map((proposal) => <article className={`proposal-card ${proposal.status}`} key={proposal.proposalId}>
        <div className="proposal-title">{proposal.status === "pending" && <input className="proposal-select" type="checkbox" checked={selected.has(proposal.proposalId)} onChange={(event) => setSelected((current) => { const next = new Set(current); event.target.checked ? next.add(proposal.proposalId) : next.delete(proposal.proposalId); return next; })} aria-label={`选择${proposalTitle(proposal)}`} />}<span>{proposalKindLabel(proposal.kind)}</span><strong>{proposalTitle(proposal)}</strong>{proposalDetail(proposal) !== "" && <small>{proposalDetail(proposal)}</small>}<i>{confidenceLabel(proposal.confidence)} · {Math.round(proposal.confidence * 100)}%</i></div>
        <div className="proposal-signals">{proposal.producers.map((producer) => <span key={producer}>{producer === "dsh" ? "DSH" : "本地"}</span>)}{proposal.producers.length > 1 && <b>重复证据已合并</b>}{conflictIds.has(proposal.proposalId) && <b className="conflict">存在替代判断</b>}</div>
        <details open={conflictIds.has(proposal.proposalId)}><summary>{proposal.evidence.length} 条正文证据</summary>{proposal.evidence.map((item) => <button className="evidence-link" key={item.evidenceId} onClick={() => onOpenEvidence(item.chapterId)}><span>{item.text}</span><small>{item.anchor} · 打开章节</small></button>)}</details>
        {proposal.status === "pending" ? <div className="proposal-actions"><button onClick={() => void review(proposal, "rejected")} disabled={busy || taskRunning}>排除</button><button className="accept" onClick={() => void review(proposal, "accepted")} disabled={busy || taskRunning}>接受</button></div> : <div className="reviewed-actions"><span className="reviewed-state">{proposal.status === "accepted" ? "已接受" : "已排除"}<small>{reviewAuditLabel(proposal)}</small></span><button onClick={() => void reopen(proposal)} disabled={busy || taskRunning}>重新审核</button></div>}
      </article>)}
      {proposals.length === 0 && <div className="reconstruction-empty">当前筛选下没有候选。</div>}
    </div>}
  </aside>;
}

function confidenceLabel(value: number): string {
  if (value >= 0.85) return "高置信";
  if (value >= 0.7) return "中置信";
  return "低置信";
}

function proposalKindLabel(kind: ReconstructionProposal["kind"]): string {
  return { entity: "人物", relation: "关系", world: "世界观", character_field: "角色字段", event: "事件" }[kind];
}

function proposalTitle(proposal: ReconstructionProposal): string {
  if (proposal.kind === "entity") return proposal.name ?? "未命名人物";
  if (proposal.kind === "relation") return `${proposal.sourceName} → ${proposal.targetName}`;
  if (proposal.kind === "world") return proposal.name ?? "未命名世界观";
  if (proposal.kind === "character_field") return `${proposal.characterName} · ${proposal.field}`;
  return proposal.title ?? "未命名事件";
}

function proposalDetail(proposal: ReconstructionProposal): string {
  if (proposal.kind === "relation") return proposal.label ?? "";
  if (proposal.kind === "world") return `${proposal.category} · ${proposal.description}`;
  if (proposal.kind === "character_field") return proposal.value ?? "";
  if (proposal.kind === "event") return `${proposal.timeLabel} · ${proposal.description} · ${[...(proposal.characterNames ?? []), ...(proposal.worldNames ?? [])].join("、")}`;
  return "";
}

function proposalVariant(proposal: ReconstructionProposal): string {
  if (proposal.kind === "relation") return `${proposal.sourceName}\0${proposal.targetName}\0${proposal.label}`;
  if (proposal.kind === "world") return proposal.description ?? "";
  if (proposal.kind === "character_field") return proposal.value ?? "";
  if (proposal.kind === "event") return `${proposal.description}\0${proposal.characterNames?.join("|")}\0${proposal.worldNames?.join("|")}`;
  return proposal.name ?? "";
}

function reviewAuditLabel(proposal: ReconstructionProposal): string {
  const mode = proposal.reviewMode === "batch" ? "批量审核" : proposal.reviewMode === "single" ? "单项审核" : "历史审核";
  if (proposal.reviewedAt === "") return mode;
  const reviewed = new Date(proposal.reviewedAt);
  return `${mode} · ${Number.isNaN(reviewed.valueOf()) ? proposal.reviewedAt : reviewed.toLocaleString("zh-CN")}`;
}

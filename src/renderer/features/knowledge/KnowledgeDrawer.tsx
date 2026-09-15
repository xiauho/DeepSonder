import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  KnowledgeCardDocument,
  KnowledgeEntity,
  KnowledgeEvent,
  KnowledgeRelation,
  KnowledgeSnapshot,
  KnowledgeWorld,
  OperationResult,
  ReconstructionEvidence,
  RpcError,
} from "../../../shared/contracts";

interface KnowledgeDrawerProps {
  refreshToken: number;
  onClose: () => void;
  onError: (error: RpcError) => void;
  onChanged: () => void;
  onOpenEvidence: (chapterId: string) => void;
}

export function KnowledgeDrawer({ refreshToken, onClose, onError, onChanged, onOpenEvidence }: KnowledgeDrawerProps) {
  const [snapshot, setSnapshot] = useState<KnowledgeSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [card, setCard] = useState<KnowledgeCardDocument | null>(null);
  const [cardContent, setCardContent] = useState("");

  const load = useCallback(async () => {
    const result = await window.novalist.getKnowledgeSnapshot();
    if (result.ok) setSnapshot(result.value); else onError(result.error);
  }, [onError]);

  useEffect(() => { void load(); }, [load, refreshToken]);

  const apply = async (pending: Promise<OperationResult<KnowledgeSnapshot>>) => {
    setBusy(true);
    const result = await pending;
    setBusy(false);
    if (!result.ok) { onError(result.error); return; }
    setSnapshot(result.value);
    onChanged();
  };

  const openCard = async (ownerKind: "character" | "world", ownerId: string, mode: "generated" | "author") => {
    setBusy(true);
    const result = await window.novalist.openKnowledgeCard(ownerKind, ownerId, mode);
    setBusy(false);
    if (!result.ok) { onError(result.error); return; }
    setCard(result.value);
    setCardContent(result.value.content);
  };

  const saveCard = async () => {
    if (card === null || card.readOnly) return;
    setBusy(true);
    const result = await window.novalist.saveKnowledgeAuthorCard(card.ownerKind, card.ownerId, cardContent, card.revision);
    setBusy(false);
    if (!result.ok) { onError(result.error); return; }
    setCard(result.value);
    setCardContent(result.value.content);
  };

  const entities = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    return snapshot?.entities.filter((item) => !needle ||
      `${item.displayName} ${item.aliases.join(" ")} ${Object.entries(item.profileFields).flat().join(" ")}`.toLocaleLowerCase("zh-CN").includes(needle)) ?? [];
  }, [query, snapshot]);
  const worlds = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    return snapshot?.worlds.filter((item) => !needle ||
      `${item.name} ${item.category} ${item.description}`.toLocaleLowerCase("zh-CN").includes(needle)) ?? [];
  }, [query, snapshot]);
  const events = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    return snapshot?.events.filter((item) => !needle ||
      `${item.timeLabel} ${item.title} ${item.description}`.toLocaleLowerCase("zh-CN").includes(needle)) ?? [];
  }, [query, snapshot]);
  const evidenceById = useMemo(
    () => new Map(snapshot?.evidence.map((item) => [item.evidenceId, item]) ?? []),
    [snapshot],
  );
  const moveEvent = (eventId: string, offset: -1 | 1) => {
    if (snapshot === null) return;
    const ids = snapshot.events.map((item) => item.eventId);
    const index = ids.indexOf(eventId);
    const target = index + offset;
    if (index < 0 || target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target]!, ids[index]!];
    void apply(window.novalist.reorderKnowledgeEvents(ids));
  };

  return <aside className="knowledge-drawer" aria-label="知识整理">
    <header><div><span className="eyebrow">CURATED KNOWLEDGE</span><h2>人物、关系、世界观与事件</h2></div><button className="icon-button" onClick={onClose}>×</button></header>
    <p className="knowledge-scope">这里只展示已经人工确认的正文知识。结构化卡片位于只读生成目录，与作者补充资料分离；名称、别名、合并、关系与事件修订会在重建后保留。</p>
    <div className="knowledge-stats"><span><strong>{snapshot?.entities.length ?? 0}</strong>人物</span><span><strong>{snapshot?.relations.length ?? 0}</strong>关系</span><span><strong>{snapshot?.worlds.length ?? 0}</strong>世界观</span><span><strong>{snapshot?.events.length ?? 0}</strong>事件</span><span><strong>{snapshot?.operationCount ?? 0}</strong>整理记录</span></div>
    <input className="knowledge-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索人物、字段、世界观或事件…" aria-label="搜索知识" />

    {(snapshot?.diagnostics.length ?? 0) > 0 && <section className="knowledge-section knowledge-diagnostics">
      <h3>知识诊断 <small>时间与语义连接</small></h3>
      <div className="knowledge-list">{snapshot!.diagnostics.map((diagnostic) => <article className={`knowledge-diagnostic ${diagnostic.severity}`} key={diagnostic.diagnosticId}><strong>{diagnostic.code === "temporal_overlap" ? "时间顺序待确认" : "语义连接待修复"}</strong><p>{diagnostic.message}</p><div className="diagnostic-evidence">{diagnostic.evidenceIds.map((id) => { const evidence = evidenceById.get(id); return evidence === undefined ? null : <button key={id} onClick={() => onOpenEvidence(evidence.chapterId)}><span>{evidence.text}</span><small>{evidence.anchor} · 打开章节</small></button>; })}</div></article>)}</div>
    </section>}

    <section className="knowledge-section">
      <h3>人物身份 <small>重命名 · 别名 · 合并</small></h3>
      <div className="knowledge-list">
        {entities.map((entity) => <EntityEditor
          key={entity.entityId}
          entity={entity}
          allEntities={snapshot?.entities ?? []}
          busy={busy}
          onRename={(name) => apply(window.novalist.renameKnowledgeEntity(entity.entityId, name))}
          onAliases={(aliases) => apply(window.novalist.setKnowledgeEntityAliases(entity.entityId, aliases))}
          onMerge={(targetId) => apply(window.novalist.mergeKnowledgeEntities(entity.entityId, targetId))}
          onUpdateField={(field, value) => apply(window.novalist.updateKnowledgeCharacterField(entity.entityId, field, value))}
          onHideField={(field) => apply(window.novalist.hideKnowledgeCharacterField(entity.entityId, field))}
          onRestoreField={(field) => apply(window.novalist.restoreKnowledgeCharacterField(entity.entityId, field))}
          onOpenCard={(mode) => void openCard("character", entity.entityId, mode)}
        />)}
        {entities.length === 0 && <div className="knowledge-empty">尚无已确认人物，或没有匹配结果。</div>}
      </div>
    </section>

    <section className="knowledge-section">
      <h3>时间线事件 <small>证据顺序 · 语义连接</small></h3>
      <div className="knowledge-list">
        {events.map((event) => { const index = snapshot!.events.findIndex((item) => item.eventId === event.eventId); return <EventCard event={event} snapshot={snapshot!} busy={busy} evidenceById={evidenceById} key={event.eventId}
          onSave={(timeLabel, title, description) => apply(window.novalist.updateKnowledgeEvent(event.eventId, timeLabel, title, description))}
          onSaveLinks={(participantIds, worldIds) => apply(window.novalist.updateKnowledgeEventLinks(event.eventId, participantIds, worldIds))}
          onOpenEvidence={onOpenEvidence}
          onMoveUp={index > 0 ? () => moveEvent(event.eventId, -1) : null}
          onMoveDown={index >= 0 && index < snapshot!.events.length - 1 ? () => moveEvent(event.eventId, 1) : null}
          onHide={() => apply(window.novalist.hideKnowledgeEvent(event.eventId))} />; })}
        {events.length === 0 && <div className="knowledge-empty">尚无已确认事件，或没有匹配结果。</div>}
      </div>
    </section>

    {(snapshot?.hiddenEvents.length ?? 0) > 0 && <section className="knowledge-section">
      <h3>已隐藏事件 <small>保留证据与连接来源</small></h3>
      <div className="knowledge-list">{snapshot!.hiddenEvents.map((event) => <article className="knowledge-card hidden-event-card" key={event.eventId}><div className="knowledge-card-title"><strong>{event.title}</strong><small>{event.timeLabel}</small></div><p>{event.description}</p><button className="primary-button" disabled={busy} onClick={() => void apply(window.novalist.restoreKnowledgeEvent(event.eventId))}>恢复事件</button></article>)}</div>
    </section>}

    <section className="knowledge-section">
      <h3>世界观条目 <small>已审核投影 · 只读生成卡</small></h3>
      <div className="knowledge-list">
        {worlds.map((world) => <WorldCard world={world} busy={busy} key={world.worldId}
          onSave={(name, category, description) => apply(window.novalist.updateKnowledgeWorld(world.worldId, name, category, description))}
          onHide={() => apply(window.novalist.hideKnowledgeWorld(world.worldId))}
          onOpenCard={(mode) => void openCard("world", world.worldId, mode)} />)}
        {worlds.length === 0 && <div className="knowledge-empty">尚无已确认世界观，或没有匹配结果。</div>}
      </div>
    </section>

    {(snapshot?.hiddenWorlds.length ?? 0) > 0 && <section className="knowledge-section">
      <h3>已隐藏世界观 <small>保留证据与整理记录</small></h3>
      <div className="knowledge-list">{snapshot!.hiddenWorlds.map((world) => <article className="knowledge-card hidden-world-card" key={world.worldId}><div className="knowledge-card-title"><strong>{world.name}</strong><small>{world.category}</small></div><p>{world.description}</p><div className="knowledge-card-actions"><button onClick={() => void openCard("world", world.worldId, "author")}>编辑作者卡</button><button className="primary-button" disabled={busy} onClick={() => void apply(window.novalist.restoreKnowledgeWorld(world.worldId))}>恢复条目</button></div></article>)}</div>
    </section>}

    {(snapshot?.merges.length ?? 0) > 0 && <section className="knowledge-section">
      <h3>已合并身份 <small>可恢复拆分</small></h3>
      <div className="merge-list">{snapshot!.merges.map((merge) => <article key={merge.sourceEntityId}><span><strong>{merge.sourceName}</strong><small>已合并到 {merge.targetName}</small></span><button disabled={busy} onClick={() => void apply(window.novalist.unmergeKnowledgeEntity(merge.sourceEntityId))}>恢复拆分</button></article>)}</div>
    </section>}

    <section className="knowledge-section">
      <h3>人物关系 <small>方向 · 名称 · 删除</small></h3>
      <div className="knowledge-list">
        {snapshot?.relations.map((relation) => <RelationEditor
          key={relation.relationId}
          relation={relation}
          entities={snapshot.entities}
          busy={busy}
          onSave={(sourceId, targetId, label) => apply(window.novalist.updateKnowledgeRelation(relation.relationId, sourceId, targetId, label))}
          onDelete={() => apply(window.novalist.deleteKnowledgeRelation(relation.relationId))}
        />)}
        {(snapshot?.relations.length ?? 0) === 0 && <div className="knowledge-empty">尚无已确认关系。</div>}
      </div>
    </section>
    {card !== null && <div className="knowledge-card-modal" role="dialog" aria-label="知识卡编辑器"><div className="knowledge-card-editor"><header><div><span className="eyebrow">{card.readOnly ? "GENERATED CARD" : "AUTHOR CARD"}</span><h3>{card.title}</h3><small>{card.relativePath}</small></div><button className="icon-button" onClick={() => setCard(null)}>×</button></header><textarea value={cardContent} readOnly={card.readOnly} onChange={(event) => setCardContent(event.target.value)} /><footer><span>{card.readOnly ? "只读生成投影；修改请返回知识字段" : "作者内容不会被正文重建覆盖"}</span>{!card.readOnly && <button className="primary-button" disabled={busy || cardContent === card.content} onClick={() => void saveCard()}>保存作者卡</button>}</footer></div></div>}
  </aside>;
}

function EntityEditor({ entity, allEntities, busy, onRename, onAliases, onMerge, onUpdateField, onHideField, onRestoreField, onOpenCard }: {
  entity: KnowledgeEntity;
  allEntities: KnowledgeEntity[];
  busy: boolean;
  onRename: (name: string) => void;
  onAliases: (aliases: string[]) => void;
  onMerge: (targetId: string) => void;
  onUpdateField: (field: string, value: string) => void;
  onHideField: (field: string) => void;
  onRestoreField: (field: string) => void;
  onOpenCard: (mode: "generated" | "author") => void;
}) {
  const [name, setName] = useState(entity.displayName);
  const [aliases, setAliases] = useState(entity.aliases.join("，"));
  const [targetId, setTargetId] = useState("");
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(entity.profileFields);
  useEffect(() => {
    setName(entity.displayName);
    setAliases(entity.aliases.join("，"));
    setFieldValues(entity.profileFields);
  }, [entity.aliases, entity.displayName, entity.profileFields]);
  const targets = allEntities.filter((item) => item.entityId !== entity.entityId);
  const parsedAliases = aliases.split(/[,，、;；\n]+/u).map((item) => item.trim()).filter(Boolean);
  return <article className="knowledge-card entity-card">
    <div className="knowledge-card-title"><strong>{entity.displayName}</strong><small>{entity.evidenceIds.length} 条证据</small></div>
    {Object.keys(entity.profileFields).length > 0 && <div className="profile-field-editor">{Object.entries(entity.profileFields).map(([field, value]) => <label key={field}>{field}<div><input value={fieldValues[field] ?? value} maxLength={200} onChange={(event) => setFieldValues((current) => ({ ...current, [field]: event.target.value }))} /><span className="field-actions"><button className="field-hide danger-text" disabled={busy} onClick={() => { if (window.confirm(`隐藏“${field}”字段？之后可以恢复。`)) onHideField(field); }}>隐藏</button><button className="field-save" disabled={busy || !(fieldValues[field] ?? value).trim() || (fieldValues[field] ?? value).trim() === value} onClick={() => onUpdateField(field, (fieldValues[field] ?? value).trim())}>保存</button></span></div></label>)}</div>}
    {Object.keys(entity.hiddenProfileFields).length > 0 && <div className="hidden-fields"><small>已隐藏字段</small>{Object.entries(entity.hiddenProfileFields).map(([field, value]) => <div key={field}><span><strong>{field}</strong>{value}</span><button disabled={busy} onClick={() => onRestoreField(field)}>恢复</button></div>)}</div>}
    <small className="generated-card-path">生成卡：{entity.cardRelativePath}</small>
    <div className="knowledge-card-actions card-open-actions"><button className="open-generated-card" disabled={busy} onClick={() => onOpenCard("generated")}>查看生成卡</button><button className="open-author-card" disabled={busy} onClick={() => onOpenCard("author")}>编辑作者卡</button></div>
    <label>显示名称<div><input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} /><button disabled={busy || !name.trim() || name.trim() === entity.displayName} onClick={() => onRename(name.trim())}>保存</button></div></label>
    <label>别名<div><input value={aliases} maxLength={500} onChange={(event) => setAliases(event.target.value)} placeholder="用逗号分隔" /><button disabled={busy || parsedAliases.length > 20} onClick={() => onAliases(parsedAliases)}>保存</button></div></label>
    {targets.length > 0 && <label>合并到<div><select value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">选择保留的人物…</option>{targets.map((item) => <option value={item.entityId} key={item.entityId}>{item.displayName}</option>)}</select><button className="danger-text" disabled={busy || !targetId} onClick={() => { if (window.confirm(`将“${entity.displayName}”合并到所选人物？此操作之后可以拆分恢复。`)) onMerge(targetId); }}>合并</button></div></label>}
  </article>;
}

function WorldCard({ world, busy, onSave, onHide, onOpenCard }: {
  world: KnowledgeWorld;
  busy: boolean;
  onSave: (name: string, category: string, description: string) => void;
  onHide: () => void;
  onOpenCard: (mode: "generated" | "author") => void;
}) {
  const [name, setName] = useState(world.name);
  const [category, setCategory] = useState(world.category);
  const [description, setDescription] = useState(world.description);
  useEffect(() => {
    setName(world.name);
    setCategory(world.category);
    setDescription(world.description);
  }, [world.category, world.description, world.name]);
  const unchanged = name.trim() === world.name && category.trim() === world.category && description.trim() === world.description;
  return <article className="knowledge-card world-card">
    <div className="knowledge-card-title"><strong>{world.name}</strong><small>{world.evidenceIds.length} 条证据</small></div>
    <label>名称<input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /></label>
    <label>分类<input value={category} maxLength={80} onChange={(event) => setCategory(event.target.value)} /></label>
    <label>描述<textarea value={description} maxLength={200} onChange={(event) => setDescription(event.target.value)} /></label>
    <small className="generated-card-path">生成卡：{world.cardRelativePath}</small>
    <div className="knowledge-card-actions card-open-actions"><button className="open-generated-card" disabled={busy} onClick={() => onOpenCard("generated")}>查看生成卡</button><button className="open-author-card" disabled={busy} onClick={() => onOpenCard("author")}>编辑作者卡</button></div>
    <div className="knowledge-card-actions"><button className="danger-text" disabled={busy} onClick={() => { if (window.confirm(`隐藏世界观“${world.name}”？之后可以恢复。`)) onHide(); }}>隐藏条目</button><button className="primary-button world-save" disabled={busy || unchanged || !name.trim() || !category.trim() || !description.trim()} onClick={() => onSave(name.trim(), category.trim(), description.trim())}>保存条目</button></div>
  </article>;
}

function EventCard({ event, snapshot, busy, evidenceById, onSave, onSaveLinks, onOpenEvidence, onMoveUp, onMoveDown, onHide }: {
  event: KnowledgeEvent;
  snapshot: KnowledgeSnapshot;
  busy: boolean;
  evidenceById: Map<string, ReconstructionEvidence>;
  onSave: (timeLabel: string, title: string, description: string) => void;
  onSaveLinks: (participantIds: string[], worldIds: string[]) => void;
  onOpenEvidence: (chapterId: string) => void;
  onMoveUp: (() => void) | null;
  onMoveDown: (() => void) | null;
  onHide: () => void;
}) {
  const [timeLabel, setTimeLabel] = useState(event.timeLabel);
  const [title, setTitle] = useState(event.title);
  const [description, setDescription] = useState(event.description);
  const [participantIds, setParticipantIds] = useState(event.participantEntityIds);
  const [worldIds, setWorldIds] = useState(event.worldIds);
  useEffect(() => {
    setTimeLabel(event.timeLabel);
    setTitle(event.title);
    setDescription(event.description);
    setParticipantIds(event.participantEntityIds);
    setWorldIds(event.worldIds);
  }, [event.description, event.participantEntityIds, event.timeLabel, event.title, event.worldIds]);
  const unchanged = timeLabel.trim() === event.timeLabel && title.trim() === event.title && description.trim() === event.description;
  const linksUnchanged = sameIds(participantIds, event.participantEntityIds) && sameIds(worldIds, event.worldIds);
  const toggle = (values: string[], id: string, checked: boolean, setter: (next: string[]) => void) => {
    setter(checked ? [...values, id] : values.filter((item) => item !== id));
  };
  return <article className="knowledge-card event-card">
    <div className="knowledge-card-title"><strong>{event.order}. {event.title}</strong><span className="event-order-actions"><button disabled={busy || onMoveUp === null} title="上移事件" onClick={() => onMoveUp?.()}>↑</button><button disabled={busy || onMoveDown === null} title="下移事件" onClick={() => onMoveDown?.()}>↓</button><small>{event.evidenceIds.length} 条证据</small></span></div>
    <label>时间标记<input value={timeLabel} maxLength={40} onChange={(value) => setTimeLabel(value.target.value)} /></label>
    <label>事件标题<input value={title} maxLength={80} onChange={(value) => setTitle(value.target.value)} /></label>
    <label>事件描述<textarea value={description} maxLength={200} onChange={(value) => setDescription(value.target.value)} /></label>
    <fieldset className="event-link-editor"><legend>参与人物</legend>{snapshot.entities.map((entity) => <label key={entity.entityId}><input type="checkbox" checked={participantIds.includes(entity.entityId)} onChange={(change) => toggle(participantIds, entity.entityId, change.target.checked, setParticipantIds)} />{entity.displayName}</label>)}{snapshot.entities.length === 0 && <small>暂无活动人物</small>}</fieldset>
    <fieldset className="event-link-editor"><legend>关联世界观</legend>{snapshot.worlds.map((world) => <label key={world.worldId}><input type="checkbox" checked={worldIds.includes(world.worldId)} onChange={(change) => toggle(worldIds, world.worldId, change.target.checked, setWorldIds)} />{world.name}</label>)}{snapshot.worlds.length === 0 && <small>暂无活动世界观</small>}</fieldset>
    <button className="event-links-save" disabled={busy || linksUnchanged} onClick={() => onSaveLinks(participantIds, worldIds)}>保存语义连接</button>
    <details className="event-evidence"><summary>{event.evidenceIds.length} 条已采用正文证据</summary>{event.evidenceIds.map((id) => { const evidence = evidenceById.get(id); return evidence === undefined ? null : <button key={id} onClick={() => onOpenEvidence(evidence.chapterId)}><span>{evidence.text}</span><small>{evidence.anchor} · 打开章节</small></button>; })}</details>
    <div className="knowledge-card-actions"><button className="danger-text" disabled={busy} onClick={() => { if (window.confirm(`隐藏事件“${event.title}”？之后可以恢复。`)) onHide(); }}>隐藏事件</button><button className="primary-button event-save" disabled={busy || unchanged || !timeLabel.trim() || !title.trim() || !description.trim()} onClick={() => onSave(timeLabel.trim(), title.trim(), description.trim())}>保存事件</button></div>
  </article>;
}

function sameIds(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const sortedLeft = [...left].sort();
  const sortedRight = [...right].sort();
  return sortedLeft.every((item, index) => item === sortedRight[index]);
}

function RelationEditor({ relation, entities, busy, onSave, onDelete }: {
  relation: KnowledgeRelation;
  entities: KnowledgeEntity[];
  busy: boolean;
  onSave: (sourceId: string, targetId: string, label: string) => void;
  onDelete: () => void;
}) {
  const [sourceId, setSourceId] = useState(relation.sourceEntityId);
  const [targetId, setTargetId] = useState(relation.targetEntityId);
  const [label, setLabel] = useState(relation.label);
  useEffect(() => { setSourceId(relation.sourceEntityId); setTargetId(relation.targetEntityId); setLabel(relation.label); }, [relation]);
  const name = (id: string) => entities.find((item) => item.entityId === id)?.displayName ?? id;
  return <article className="knowledge-card relation-card">
    <div className="knowledge-card-title"><strong>{name(relation.sourceEntityId)} → {name(relation.targetEntityId)}</strong><small>{relation.evidenceIds.length} 条证据</small></div>
    <div className="relation-route"><select value={sourceId} onChange={(event) => setSourceId(event.target.value)}>{entities.map((item) => <option value={item.entityId} key={item.entityId}>{item.displayName}</option>)}</select><span>→</span><select value={targetId} onChange={(event) => setTargetId(event.target.value)}>{entities.map((item) => <option value={item.entityId} key={item.entityId}>{item.displayName}</option>)}</select></div>
    <label>关系名称<input value={label} maxLength={80} onChange={(event) => setLabel(event.target.value)} /></label>
    <div className="knowledge-card-actions"><button className="danger-text" disabled={busy} onClick={() => { if (window.confirm("删除这条人工知识关系？原始候选仍保留在审核记录中。")) onDelete(); }}>删除关系</button><button className="primary-button" disabled={busy || !label.trim() || sourceId === targetId} onClick={() => onSave(sourceId, targetId, label.trim())}>保存关系</button></div>
  </article>;
}

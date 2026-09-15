import { FormEvent, useState } from "react";
import type {
  ManuscriptImportPlan,
  ManuscriptMutationResult,
  OpenedProjectV2,
  RpcError,
} from "../../../shared/contracts";

type ImportWizardProps = {
  mode?: "create";
  onClose: () => void;
  onCreated: (project: OpenedProjectV2) => void;
  onError: (error: RpcError) => void;
} | {
  mode: "append";
  afterChapterId?: string;
  onClose: () => void;
  onAppended: (result: ManuscriptMutationResult) => void;
  onError: (error: RpcError) => void;
};

export function ImportWizard(props: ImportWizardProps) {
  const { onClose, onError } = props;
  const appendMode = props.mode === "append";
  const [plan, setPlan] = useState<ManuscriptImportPlan | null>(null);
  const [name, setName] = useState("");
  const [author, setAuthor] = useState("");
  const [busy, setBusy] = useState(false);

  const scan = async (sourceType: "file" | "directory") => {
    setBusy(true);
    const result = await window.novalist.chooseAndScanManuscript(sourceType);
    setBusy(false);
    if (!result.ok) { onError(result.error); return; }
    if (result.value === null) return;
    setPlan(result.value);
    if (!name) setName(result.value.suggestedName);
    if (!author && result.value.sourceKind === "novalist_v1_manuscript") {
      setAuthor(result.value.suggestedAuthor);
    }
  };

  const commit = async (event: FormEvent) => {
    event.preventDefault();
    if (plan === null || (!appendMode && !name.trim())) return;
    setBusy(true);
    if (appendMode) {
      const result = await window.novalist.appendManuscript(plan.digest, props.afterChapterId);
      setBusy(false);
      if (!result.ok) { onError(result.error); return; }
      props.onAppended(result.value);
      return;
    }
    const result = await window.novalist.createProjectV2(name, author, plan.digest);
    setBusy(false);
    if (!result.ok) { onError(result.error); return; }
    if (result.value !== null) props.onCreated(result.value);
  };

  return <div className="modal-layer import-layer" role="presentation">
    <section className="import-wizard" role="dialog" aria-modal="true" aria-label={appendMode ? "追加导入正文" : "导入正文建立新项目"}>
      <header><div><span className="eyebrow">MANUSCRIPT-ONLY IMPORT</span><h2>{appendMode ? "向当前项目追加正文" : "从正文建立新项目"}</h2></div><button className="icon-button" onClick={onClose}>×</button></header>
      {plan === null ? <div className="import-source-step">
        <p>{appendMode ? "只追加正文，不导入来源中的人物卡、世界观、时间线或记忆。完成后现有识别结果会标记为过期。" : "旧人物卡、世界观、体系、时间线和记忆不会复制。新项目只导入正文，后续从证据重新识别。"}</p>
        <button className="source-card" onClick={() => void scan("directory")} disabled={busy}><strong>旧项目或正文目录</strong><span>扫描目录；旧项目只读取章节的“正文”部分</span></button>
        <button className="source-card" onClick={() => void scan("file")} disabled={busy}><strong>单个 Markdown / TXT</strong><span>支持 UTF-8、BOM 和 GB18030 中文文本</span></button>
      </div> : <form onSubmit={(event) => void commit(event)}>
        <div className="import-summary"><strong>{plan.chapters.length} 章</strong><span>{formatBytes(plan.totalSourceBytes)} · {plan.sourceKind === "novalist_v1_manuscript" ? "旧项目正文" : "外部书稿"}</span><button type="button" onClick={() => setPlan(null)}>重新选择</button></div>
        {plan.warnings.length > 0 && <div className="import-warnings">{plan.warnings.map((warning) => <p key={`${warning.code}-${warning.source}`}>{warning.source}：{warning.message}</p>)}</div>}
        <div className="import-chapters">{plan.chapters.map((chapter) => <article key={chapter.chapterId} className={chapter.empty ? "empty" : ""}><span>{String(chapter.sequence).padStart(2, "0")}</span><div><strong>{chapter.title}</strong><small>{chapter.sourceName} · {chapter.encoding}</small><p>{chapter.excerpt || "未识别到正文"}</p></div></article>)}</div>
        {!appendMode && <div className="import-project-fields"><label>新项目名称<input value={name} maxLength={200} onChange={(event) => setName(event.target.value)} /></label><label>作者<input value={author} maxLength={500} onChange={(event) => setAuthor(event.target.value)} placeholder="可选" /></label></div>}
        <p className="import-commit-note">提交时会重新扫描来源；如果正文发生变化，本次计划将失效。{appendMode ? ` 新章节将插入到${props.afterChapterId ? "当前章节之后" : "正文末尾"}。` : ""}</p>
        <div className="dialog-actions"><button type="button" className="ghost-button" onClick={onClose}>取消</button><button className="primary-button" disabled={busy || (!appendMode && !name.trim()) || plan.chapters.some((item) => item.empty)}>{busy ? "正在校验…" : appendMode ? "确认追加" : "选择位置并创建"}</button></div>
      </form>}
    </section>
  </div>;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

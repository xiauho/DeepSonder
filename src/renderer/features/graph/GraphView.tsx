import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  GraphEdge,
  GraphNode,
  RelationshipGraphSnapshot,
  RpcError,
} from "../../../shared/contracts";
import {
  chooseGraphLayoutName,
  usesLargeGraphLayout,
} from "../../../shared/graph-layout-policy";

interface GraphViewProps {
  projectRoot: string;
  refreshToken: number;
  onOpenCharacter: (node: GraphNode) => void;
  onOpenEvidence: (chapterId: string) => void;
  onError: (error: RpcError) => void;
}

type Selection = { kind: "node" | "edge"; id: string } | null;

export function GraphView({
  projectRoot,
  refreshToken,
  onOpenCharacter,
  onOpenEvidence,
  onError,
}: GraphViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<Core | null>(null);
  const [snapshot, setSnapshot] = useState<RelationshipGraphSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [relationType, setRelationType] = useState("");
  const [nodeKind, setNodeKind] = useState<"" | GraphNode["nodeKind"]>("");
  const [selection, setSelection] = useState<Selection>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void window.novalist.getRelationshipGraph().then((result) => {
      if (!active) return;
      setLoading(false);
      if (!result.ok) {
        onError(result.error);
        return;
      }
      setSnapshot(result.value);
      setSelection(null);
    });
    return () => { active = false; };
  }, [onError, projectRoot, refreshToken]);

  const nodesById = useMemo(
    () => new Map(snapshot?.nodes.map((node) => [node.id, node]) ?? []),
    [snapshot],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || snapshot === null || snapshot.nodes.length === 0) return;
    const elements: ElementDefinition[] = [
      ...snapshot.nodes.map((node) => ({
        data: {
          id: node.id,
          label: node.name,
          resolved: node.resolved ? 1 : 0,
          kind: node.nodeKind,
          order: node.order ?? 0,
        },
      })),
      ...snapshot.edges.map((edge) => ({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: edge.label,
        },
      })),
    ];
    const layoutName = chooseGraphLayoutName(snapshot.nodes.length);
    const graph = cytoscape({
      container,
      elements,
      minZoom: 0.25,
      maxZoom: 2.6,
      wheelSensitivity: 0.18,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#315f52",
            "border-color": "#fffdf9",
            "border-width": 3,
            color: "#fff",
            label: "data(label)",
            "font-family": "Microsoft YaHei UI",
            "font-size": 11,
            height: 58,
            width: 58,
            "text-outline-color": "#315f52",
            "text-outline-width": 2,
          },
        },
        {
          selector: "node[resolved = 0]",
          style: {
            "background-color": "#f5f1ea",
            "border-color": "#ad9f8e",
            "border-style": "dashed",
            color: "#796f63",
            "text-outline-width": 0,
          },
        },
        {
          selector: "node[kind = 'world']",
          style: {
            "background-color": "#486b86",
            "text-outline-color": "#486b86",
            shape: "round-rectangle",
            width: 72,
            height: 48,
          },
        },
        {
          selector: "node[kind = 'event']",
          style: {
            "background-color": "#a67831",
            "text-outline-color": "#a67831",
            shape: "diamond",
            width: 66,
            height: 66,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.6,
            "line-color": "#91a79f",
            "target-arrow-color": "#66887c",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            color: "#746d65",
            "font-family": "Microsoft YaHei UI",
            "font-size": 8,
            "text-background-color": "#f8f5ef",
            "text-background-opacity": 0.9,
            "text-background-padding": "3px",
            "text-rotation": "autorotate",
          },
        },
        {
          selector: ":selected",
          style: {
            "border-color": "#d5a348",
            "border-width": 4,
            "line-color": "#d5a348",
            "target-arrow-color": "#d5a348",
          },
        },
        { selector: ".filtered-out", style: { display: "none" } },
      ],
      layout: layoutName === "cose"
        ? {
          name: "cose",
          animate: false,
          fit: true,
          padding: 48,
          nodeRepulsion: () => 5600,
          idealEdgeLength: () => 130,
        }
        : {
          name: layoutName,
          animate: false,
          fit: true,
          padding: 48,
        },
    });
    graph.on("tap", "node", (event) => {
      setSelection({ kind: "node", id: event.target.id() });
    });
    graph.on("tap", "edge", (event) => {
      setSelection({ kind: "edge", id: event.target.id() });
    });
    graph.on("tap", (event) => {
      if (event.target === graph) setSelection(null);
    });
    graphRef.current = graph;
    return () => {
      graphRef.current = null;
      graph.destroy();
    };
  }, [snapshot]);

  useEffect(() => {
    const graph = graphRef.current;
    if (graph === null || snapshot === null) return;
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    const typeNodes = new Set(snapshot.nodes.filter((node) => !nodeKind || node.nodeKind === nodeKind).map((node) => node.id));
    const matchingNodes = new Set(
      snapshot.nodes
        .filter((node) => typeNodes.has(node.id) && (!needle || `${node.name} ${node.aliases.join(" ")} ${node.state} ${node.location}`
          .toLocaleLowerCase("zh-CN").includes(needle))
        )
        .map((node) => node.id),
    );
    const visibleEdges = new Set(
      snapshot.edges
        .filter((edge) => (!relationType || edge.label === relationType) &&
          (!nodeKind || typeNodes.has(edge.source) || typeNodes.has(edge.target)) &&
          (!needle || matchingNodes.has(edge.source) || matchingNodes.has(edge.target) ||
            edge.label.toLocaleLowerCase("zh-CN").includes(needle)))
        .map((edge) => edge.id),
    );
    const visibleNodes = new Set<string>(
      needle || nodeKind ? matchingNodes : relationType ? [] : snapshot.nodes.map((node) => node.id),
    );
    for (const edge of snapshot.edges) {
      if (visibleEdges.has(edge.id)) {
        visibleNodes.add(edge.source);
        visibleNodes.add(edge.target);
      }
    }
    graph.batch(() => {
      graph.nodes().forEach((node) => {
        node.toggleClass("filtered-out", !visibleNodes.has(node.id()));
      });
      graph.edges().forEach((edge) => {
        edge.toggleClass("filtered-out", !visibleEdges.has(edge.id()));
      });
    });
  }, [nodeKind, query, relationType, snapshot]);

  const selectedNode = selection?.kind === "node"
    ? nodesById.get(selection.id) ?? null
    : null;
  const selectedEdge = selection?.kind === "edge"
    ? snapshot?.edges.find((edge) => edge.id === selection.id) ?? null
    : null;

  const focusNode = (node: GraphNode) => {
    const graph = graphRef.current;
    if (graph === null) return;
    const target = graph.getElementById(node.id);
    if (target.empty()) return;
    graph.elements().unselect();
    target.select();
    graph.animate({ center: { eles: target }, zoom: Math.max(graph.zoom(), 1.15) }, { duration: 260 });
    setSelection({ kind: "node", id: node.id });
  };

  return <section className="relationship-graph">
    <header className="graph-toolbar">
      <div><span className="eyebrow">REVIEWED SEMANTIC PROJECTION</span><h1>故事知识图谱</h1><p>{snapshot?.projectName ?? "正在读取项目知识…"}{snapshot !== null && usesLargeGraphLayout(snapshot.nodes.length) ? " · 大型图谱使用快速布局" : ""}</p></div>
      <div className="graph-filters">
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索人物、世界观或事件…" aria-label="搜索知识图谱" />
        <select value={nodeKind} onChange={(event) => setNodeKind(event.target.value as "" | GraphNode["nodeKind"])} aria-label="筛选节点类型"><option value="">全部节点</option><option value="character">人物</option><option value="world">世界观</option><option value="event">事件</option></select>
        <select value={relationType} onChange={(event) => setRelationType(event.target.value)} aria-label="筛选关系类型"><option value="">全部关系</option>{snapshot?.relationTypes.map((value) => <option value={value} key={value}>{value}</option>)}</select>
        <button onClick={() => graphRef.current?.fit(undefined, 42)}>适配</button>
        <button aria-label="放大图谱" onClick={() => graphRef.current?.zoom({ level: Math.min(2.6, (graphRef.current?.zoom() ?? 1) * 1.2), renderedPosition: { x: 320, y: 240 } })}>＋</button>
        <button aria-label="缩小图谱" onClick={() => graphRef.current?.zoom({ level: Math.max(.25, (graphRef.current?.zoom() ?? 1) / 1.2), renderedPosition: { x: 320, y: 240 } })}>－</button>
      </div>
    </header>
    <div className="graph-workbench">
      <aside className="graph-index">
        <div className="graph-stat"><strong>{snapshot?.nodes.filter((node) => node.nodeKind === "character").length ?? 0}</strong><span>人物</span><strong>{snapshot?.nodes.filter((node) => node.nodeKind === "event").length ?? 0}</strong><span>事件</span></div>
        <nav>{snapshot?.nodes.filter((node) => !nodeKind || node.nodeKind === nodeKind).map((node) => <button className={selection?.id === node.id ? "active" : ""} key={node.id} onClick={() => focusNode(node)}><i className={`${node.resolved ? "resolved" : "unresolved"} ${node.nodeKind}`} /><span><strong>{node.name}</strong><small>{node.nodeKind === "event" ? node.state : node.nodeKind === "world" ? node.state : node.path !== null ? node.location || "已有角色卡" : node.resolved ? "已审阅人物" : "未建立角色卡"}</small></span></button>)}</nav>
        {(snapshot?.warnings.length ?? 0) > 0 && <details className="graph-warnings"><summary>{snapshot!.warnings.length} 条数据提示</summary>{snapshot!.warnings.map((warning, index) => <p key={`${warning.code}-${index}`}>{warning.message}</p>)}</details>}
      </aside>
      <div className="graph-stage">
        {loading && <div className="graph-overlay">正在构建只读关系投影…</div>}
        {!loading && snapshot?.nodes.length === 0 && <div className="graph-overlay">当前项目还没有可投影的人物关系。</div>}
        <div ref={containerRef} className="graph-cytoscape" aria-label="故事知识图谱画布" />
        {(snapshot?.nodes.some((node) => node.nodeKind === "event") ?? false) && <div className="event-sequence" aria-label="事件序列"><strong>事件序列</strong>{snapshot!.nodes.filter((node) => node.nodeKind === "event").sort((left, right) => (left.order ?? 0) - (right.order ?? 0)).map((node) => <button className={selection?.id === node.id ? "active" : ""} key={node.id} onClick={() => focusNode(node)}><small>{node.order}</small><span>{node.state}</span><b>{node.name}</b></button>)}</div>}
        <span className="graph-readonly-badge">只读 · 拖动不会修改项目</span>
      </div>
      <GraphInspector node={selectedNode} edge={selectedEdge} nodesById={nodesById} onOpenCharacter={onOpenCharacter} onOpenEvidence={onOpenEvidence} />
    </div>
  </section>;
}

function GraphInspector({
  node,
  edge,
  nodesById,
  onOpenCharacter,
  onOpenEvidence,
}: {
  node: GraphNode | null;
  edge: GraphEdge | null;
  nodesById: Map<string, GraphNode>;
  onOpenCharacter: (node: GraphNode) => void;
  onOpenEvidence: (chapterId: string) => void;
}) {
  if (node !== null) {
    if (node.nodeKind === "event") return <aside className="graph-inspector"><span className="eyebrow">TIMELINE EVENT · {node.order}</span><h2>{node.name}</h2><p className="relation-label">{node.state}</p><p className="graph-note">{node.location}</p></aside>;
    if (node.nodeKind === "world") return <aside className="graph-inspector"><span className="eyebrow">WORLD CONCEPT</span><h2>{node.name}</h2><p className="relation-label">{node.state}</p><p className="graph-note">{node.location}</p></aside>;
    return <aside className="graph-inspector"><span className="eyebrow">CHARACTER</span><h2>{node.name}</h2>{node.aliases.length > 0 && <p className="graph-meta">别名：{node.aliases.join("、")}</p>}<dl><dt>位置</dt><dd>{node.location || "未记录"}</dd><dt>状态</dt><dd>{node.state || "未记录"}</dd></dl>{node.path !== null ? <button className="primary-button" onClick={() => onOpenCharacter(node)}>打开人物卡</button> : node.resolved ? <p className="graph-note">该人物已由正文证据审核确认；可在知识整理中查看生成卡或作者卡。</p> : <p className="graph-note">该人物来自关系数据，但项目中尚无对应人物卡。</p>}</aside>;
  }
  if (edge !== null) {
    const source = nodesById.get(edge.source)?.name ?? "未知节点";
    const target = nodesById.get(edge.target)?.name ?? "未知节点";
    return <aside className="graph-inspector"><span className="eyebrow">{edge.edgeKind === "relationship" ? "DIRECTED RELATION" : "SEMANTIC LINK"}</span><h2>{source} → {target}</h2><p className="relation-label">{edge.label}</p><p className="graph-meta">来源：{edge.sourceKind === "reviewed_v2" ? "已审核正文证据" : edge.sourceKind === "story_state" ? "当前故事状态" : "人物卡"}</p><h3>已采用证据</h3>{edge.evidence.length === 0 ? <p className="graph-note">暂无可验证的已采用章节证据。</p> : edge.evidence.map((item) => <button className="graph-evidence graph-evidence-link" key={`${item.chapterId}-${item.anchor}`} onClick={() => onOpenEvidence(item.chapterId)}><strong>{item.chapterId}</strong><span>{item.description}</span><small>{item.anchor} · {item.certainty} · 打开章节</small></button>)}</aside>;
  }
  return <aside className="graph-inspector graph-inspector-empty"><span className="eyebrow">INSPECTOR</span><h2>选择人物或关系</h2><p>查看人物当前状态、关系方向及已采用的章节证据。</p></aside>;
}

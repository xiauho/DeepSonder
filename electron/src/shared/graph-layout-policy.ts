export const LARGE_GRAPH_NODE_THRESHOLD = 200;

export type GraphLayoutName = "circle" | "cose" | "grid";

export function chooseGraphLayoutName(nodeCount: number): GraphLayoutName {
  if (!Number.isFinite(nodeCount) || nodeCount < 0) {
    throw new Error("Graph node count must be a non-negative finite number.");
  }
  if (nodeCount < 3) return "circle";
  if (nodeCount <= LARGE_GRAPH_NODE_THRESHOLD) return "cose";
  return "grid";
}

export function usesLargeGraphLayout(nodeCount: number): boolean {
  return chooseGraphLayoutName(nodeCount) === "grid";
}

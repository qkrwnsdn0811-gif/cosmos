/**
 * 원형 2겹 배치 — 중심은 이벤트, 안쪽 원은 기사에 언급된 기업(근거), 바깥 원은 공급망으로 추정한 기업.
 * 추정 기업은 자신이 퍼져 나온 언급 기업(anchor) 쪽 부채꼴 안에 모아 둬서 점선이 서로 엉키지 않게 한다.
 * import 가 없는 순수 함수라 node --test 로 바로 검증한다 (FrontEnd/tests/radialLayout.test.ts).
 */
export const INNER_RADIUS = 0.24;
export const OUTER_RADIUS = 0.42;

export interface LayoutInput {
  id: string;
  basis: "REPORTED" | "INFERRED";
  anchorId: string | null;
  /** 2단계 추정의 중간 기업. 같은 부채꼴 안에서 그 기업 바로 다음 자리에 둔다 */
  viaId?: string | null;
}

/** 부채꼴 안 순서: 1단계 추정 기업마다 바로 뒤에 그 기업을 거친 2단계 기업을 붙인다(점선이 짧아진다) */
function ringOrder(members: LayoutInput[]): LayoutInput[] {
  const ids = new Set(members.map((m) => m.id));
  const children = new Map<string, LayoutInput[]>();
  const roots: LayoutInput[] = [];
  for (const m of members) {
    if (m.viaId && m.viaId !== m.id && ids.has(m.viaId)) {
      children.set(m.viaId, [...(children.get(m.viaId) ?? []), m]);
    } else {
      roots.push(m);
    }
  }
  const ordered: LayoutInput[] = [];
  for (const r of roots) ordered.push(r, ...(children.get(r.id) ?? []));
  // 중간 기업 자신이 다른 기업의 자식인 경우처럼 위에서 빠진 노드는 끝에 붙인다
  for (const m of members) if (!ordered.includes(m)) ordered.push(m);
  return ordered;
}

export interface Placed {
  id: string;
  x: number;
  y: number;
  ring: 1 | 2;
}

const TOP = -Math.PI / 2;
const FULL = Math.PI * 2;

export function radialLayout(nodes: LayoutInput[], size: number): Placed[] {
  const c = size / 2;
  const at = (id: string, angle: number, radius: number, ring: 1 | 2): Placed => ({
    id,
    x: c + radius * Math.cos(angle),
    y: c + radius * Math.sin(angle),
    ring,
  });
  const reported = nodes.filter((n) => n.basis === "REPORTED");
  const inferred = nodes.filter((n) => n.basis === "INFERRED");
  const placed = new Map<string, Placed>();

  if (!reported.length) {
    inferred.forEach((n, i) => placed.set(n.id, at(n.id, TOP + (i * FULL) / inferred.length, OUTER_RADIUS * size, 2)));
    return nodes.map((n) => placed.get(n.id)!);
  }

  // 추정 기업은 앵커 언급 기업 쪽에 모은다. 앵커가 없거나 목록에 없으면 마지막 언급 기업 쪽에 둔다
  const fallback = reported[reported.length - 1].id;
  const groups = new Map<string, LayoutInput[]>(reported.map((r) => [r.id, []]));
  for (const n of inferred) {
    const anchor = n.anchorId && groups.has(n.anchorId) ? n.anchorId : fallback;
    groups.get(anchor)!.push(n);
  }
  // 부채꼴 폭은 (1 + 그 기업의 추정 수)에 비례한다 — 추정이 몰린 언급 기업이 더 넓은 자리를 받는다
  const weight = (id: string) => 1 + groups.get(id)!.length;
  const total = reported.reduce((sum, r) => sum + weight(r.id), 0);
  let start = TOP - (weight(reported[0].id) / total) * Math.PI;   // 첫 언급 기업의 중심이 맨 위에 오도록
  for (const r of reported) {
    const width = (weight(r.id) / total) * FULL;
    placed.set(r.id, at(r.id, start + width / 2, INNER_RADIUS * size, 1));
    const members = ringOrder(groups.get(r.id)!);
    members.forEach((n, j) => {
      placed.set(n.id, at(n.id, start + ((j + 0.5) * width) / members.length, OUTER_RADIUS * size, 2));
    });
    start += width;
  }
  return nodes.map((n) => placed.get(n.id)!);
}

export interface LinkInput {
  basis: "REPORTED" | "INFERRED";
  hop: number;
  anchorId: string | null;
  viaId: string | null;
}

/** 점선이 이어질 노드. 2단계 추정은 중간 기업이 지도에 있으면 그쪽, 아니면 앵커. 언급 기업과 앵커 없는 추정은 null(중심). */
export function linkTargetId(node: LinkInput, onMap: Set<string>): string | null {
  if (node.basis !== "INFERRED") return null;
  if (node.hop >= 2 && node.viaId && onMap.has(node.viaId)) return node.viaId;
  return node.anchorId && onMap.has(node.anchorId) ? node.anchorId : null;
}

/** 2단계 경로의 중간 기업 = 첫 단계에서 앵커가 아닌 쪽 끝. 1단계 이하면 null. */
export function viaCompanyId(
  path: { sourceCompanyId: string | null; targetCompanyId: string | null }[],
  anchorId: string | null,
): string | null {
  if (path.length < 2) return null;
  const first = path[0];
  return first.sourceCompanyId === anchorId ? first.targetCompanyId : first.sourceCompanyId;
}

/** 이름표 방향: 원의 오른쪽은 노드 오른쪽(start), 왼쪽은 노드 왼쪽(end), 가운데 띠는 노드 아래(middle). */
export function labelSide(x: number, size: number): "start" | "middle" | "end" {
  const c = size / 2;
  if (x > c + 0.15 * size) return "start";
  if (x < c - 0.15 * size) return "end";
  return "middle";
}

/** 10자를 넘는 이름은 9자 + "…". 전체 이름은 노드의 title(툴팁)과 근거 패널에서 보인다. */
export function shortName(name: string, max = 10): string {
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}

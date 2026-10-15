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

  const step = reported.length ? FULL / reported.length : 0;
  const angleOf = new Map<string, number>();
  reported.forEach((n, i) => {
    const angle = TOP + i * step;
    angleOf.set(n.id, angle);
    placed.set(n.id, at(n.id, angle, INNER_RADIUS * size, 1));
  });

  if (!reported.length) {
    inferred.forEach((n, i) => placed.set(n.id, at(n.id, TOP + (i * FULL) / inferred.length, OUTER_RADIUS * size, 2)));
  } else {
    const fallback = reported[reported.length - 1].id;
    const groups = new Map<string, LayoutInput[]>(reported.map((r) => [r.id, []]));
    for (const n of inferred) {
      const anchor = n.anchorId && groups.has(n.anchorId) ? n.anchorId : fallback;
      groups.get(anchor)!.push(n);
    }
    for (const [anchor, members] of groups) {
      const center = angleOf.get(anchor)!;
      members.forEach((n, j) => {
        const angle = center - step / 2 + ((j + 0.5) * step) / members.length;
        placed.set(n.id, at(n.id, angle, OUTER_RADIUS * size, 2));
      });
    }
  }
  return nodes.map((n) => placed.get(n.id)!);
}

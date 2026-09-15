import * as THREE from "three";

/**
 * 관계 간선의 곡선 정의 — 빔(RelationLines)·화살촉·툴팁이 같은 곡선 위의 점을 얻도록 한곳에 둔다.
 * 두 노드를 잇는 2차 베지어 아크이고, 제어점은 원점 바깥으로 살짝 부풀린다.
 * 기업 중심 뷰에서 궤도 위 두 행성을 잇는 현(chord)은 그대로 두면 중심 행성을 뚫고 지나가 "중심과 연결된 선" 처럼 읽힌다.
 * 그래서 clear 를 주면 곡선을 표본해 원점에 가장 가까운 점을 찾고, 그 점의 반지름이 양끝 중 안쪽 반지름의 clear 배 이상이 되도록
 * 제어점을 현에 수직인 방향으로 더 밀어 궤도를 따라 돌려 그린다. 은하 뷰(clear 0)는 그대로다 — 핵을 가로지르는 빔이 원래 모양이다.
 * 끝점은 Vec3 도 되고, 여러 점이 이어 붙은 좌표 버퍼(live.pos 등) + 오프셋도 된다 — 드래그로 움직인 끝점을 복사 없이 읽기 위해서다.
 */
export const BOW = 0.16;
/**
 * 모델 종류별 중심 회피 비율(controlPoint 의 clear). 기업 중심 뷰 0.6 이면 depth 1 궤도(r ≈ 24~30) 위 두 행성의 현이
 * 중심에서 14 이상 떨어져 중심 행성(반지름 3.4)과 그 이름표를 비켜 간다. 빔·화살촉·피킹이 같은 값을 써야 한 곡선이 된다
 */
export const CHORD_CLEAR: Record<"galaxy" | "system", number> = { galaxy: 0, system: 0.6 };
/** 최근접점을 찾는 표본 수 — 화살촉이 프레임마다 간선 수만큼 부르므로 적게 둔다 */
const CLEAR_SAMPLES = 8;

const A = new THREE.Vector3();
const B = new THREE.Vector3();
const OUT = new THREE.Vector3();
const DIR = new THREE.Vector3();
const TMP = new THREE.Vector3();
const S = new THREE.Vector3();

/** 제어점을 out 에 쓰고 두 끝점 거리를 돌려준다. clear 는 CHORD_CLEAR[model.kind] (0 이면 중심 회피 없음) */
export function controlPoint(from: ArrayLike<number>, to: ArrayLike<number>, out: THREE.Vector3, fromOff = 0, toOff = 0, clear = 0) {
  A.fromArray(from, fromOff);
  B.fromArray(to, toOff);
  const dist = A.distanceTo(B);
  out.addVectors(A, B).multiplyScalar(0.5);
  OUT.copy(out);
  if (OUT.lengthSq() < 1e-6) OUT.set(0, 1, 0);
  OUT.normalize().multiplyScalar(dist * BOW);
  OUT.y += dist * BOW * 0.35;
  out.add(OUT);
  if (clear <= 0) return dist;

  // 중심 회피 — 양끝 중 안쪽 반지름의 clear 배가 목표. 한쪽 끝이 중심(원점)이면 목표가 0 이라 그대로 둔다
  const target = Math.min(Math.hypot(A.x, A.z), Math.hypot(B.x, B.z)) * clear;
  if (target <= 1e-3) return dist;
  let minR = Infinity;
  let tMin = 0.5;
  let sx = 0;
  let sz = 0;
  for (let k = 1; k < CLEAR_SAMPLES; k += 1) {
    const t = k / CLEAR_SAMPLES;
    bezier(from, to, out, t, S, fromOff, toOff);
    const r = Math.hypot(S.x, S.z);
    if (r < minR) {
      minR = r;
      tMin = t;
      sx = S.x;
      sz = S.z;
    }
  }
  if (minR >= target) return dist;
  // 현에 수직인(xz) 방향으로 민다 — 원점을 지나는 현은 최근접점의 바깥 방향이 현과 나란해서, 그쪽으로 밀면 교차점만 현을 따라 미끄러지고
  // 곡선은 여전히 원점을 지난다. 부호는 기본 부풀림이 이미 향한 쪽, 그게 없으면 중점이 원점에서 벗어난 쪽, 그것도 없으면 +z
  DIR.set(-(B.z - A.z), 0, B.x - A.x);
  if (DIR.lengthSq() < 1e-6) DIR.set(0, 0, 1);
  DIR.normalize();
  TMP.set(out.x - (A.x + B.x) / 2, 0, out.z - (A.z + B.z) / 2);
  let side = TMP.dot(DIR);
  if (Math.abs(side) < 1e-3) side = (A.x + B.x) * DIR.x + (A.z + B.z) * DIR.z;
  if (side < 0) DIR.negate();
  // 최근접점 p 를 DIR 로 δ 만큼 옮겨 |p| = target 이 되게 한다: δ² + 2δ(p·DIR) + |p|² − target² = 0.
  // 그 점에서 제어점의 가중치가 2t(1−t) 이므로 제어점은 δ 를 그만큼 나눈 거리로 민다
  const pd = sx * DIR.x + sz * DIR.z;
  const delta = -pd + Math.sqrt(Math.max(0, pd * pd + target * target - minR * minR));
  out.addScaledVector(DIR, delta / (2 * tMin * (1 - tMin)));
  return dist;
}

/** 곡선 위 t(0~1) 지점 */
export function bezier(from: ArrayLike<number>, to: ArrayLike<number>, ctrl: THREE.Vector3, t: number, out: THREE.Vector3, fromOff = 0, toOff = 0) {
  const it = 1 - t;
  out.set(0, 0, 0);
  out.addScaledVector(A.fromArray(from, fromOff), it * it);
  out.addScaledVector(ctrl, 2 * it * t);
  out.addScaledVector(B.fromArray(to, toOff), t * t);
  return out;
}

/** 점수(0~1) → 튜브 반지름(월드 단위). 가까이 가면 굵어지고 멀면 가늘어진다 */
export function radiusOf(s: number) {
  return 0.09 + s * s * 0.4;
}

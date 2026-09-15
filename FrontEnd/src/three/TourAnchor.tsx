import { useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { SceneModel } from "@/lib/graph";
import { bezier, CHORD_CLEAR, controlPoint } from "./edgeCurve";
import { liveFor } from "./morph";
import { tourAnchor } from "./tourState";

const P = new THREE.Vector3();
const Q = new THREE.Vector3();
const CTRL = new THREE.Vector3();
const RIGHT = new THREE.Vector3();
const TAU = Math.PI * 2;

/**
 * 투어 스포트라이트 앵커 — tourAnchor.target(원반·간선·행성)을 프레임마다 화면 좌표로 투영해 tourAnchor 에 쓴다.
 * 끝점은 node.pos 가 아니라 live 버퍼에서 읽는다 — 주가 고도로 떠 있는 행성, 끌어 옮긴 행성 위에 정확히 구멍이 뚫리게.
 * 대상이 없으면 아무 일도 하지 않으므로 항상 마운트해 둬도 비용이 없다.
 */
export default function TourAnchor({ model }: { model: SceneModel }) {
  const nodeIndex = useMemo(() => new Map(model.nodes.map((n, i) => [n.id, i])), [model]);

  useFrame((state) => {
    const t = tourAnchor.target;
    if (!t) {
      tourAnchor.visible = false;
      return;
    }
    const { camera, size } = state;
    const live = liveFor(model);
    // NDC → 캔버스 CSS px. z ≥ 1 이면 카메라 뒤라 구멍을 뚫지 않는다
    const project = (v: THREE.Vector3) => {
      v.project(camera);
      return { x: ((v.x + 1) / 2) * size.width, y: ((1 - v.y) / 2) * size.height, front: v.z < 1 };
    };
    const write = (x: number, y: number, r: number, visible: boolean) => {
      tourAnchor.x = x;
      tourAnchor.y = y;
      tourAnchor.r = r;
      tourAnchor.visible = visible;
    };

    if (t.kind === "disc") {
      const c = project(P.set(0, 0, 0));
      const R = model.extent.maxR;
      let r = 0;
      for (let k = 0; k < 8; k += 1) {
        const a = (k / 8) * TAU;
        const s = project(Q.set(Math.cos(a) * R, 0, Math.sin(a) * R));
        r = Math.max(r, Math.hypot(s.x - c.x, s.y - c.y));
      }
      write(c.x, c.y, r * 1.02 + 12, c.front);
      return;
    }
    if (t.kind === "node") {
      const i = nodeIndex.get(t.id);
      if (i === undefined) {
        tourAnchor.visible = false;
        return;
      }
      P.fromArray(live.pos, i * 3);
      // 화면 반지름은 카메라 오른쪽으로 행성 반지름만큼 떨어진 점을 함께 투영해 잰다
      RIGHT.setFromMatrixColumn(camera.matrixWorld, 0).normalize();
      Q.copy(P).addScaledVector(RIGHT, model.nodes[i].size * 1.3);
      const c = project(P);
      const e = project(Q);
      write(c.x, c.y, Math.max(44, Math.hypot(e.x - c.x, e.y - c.y) * 3.2), c.front);
      return;
    }
    const edge = model.edgeById.get(t.id);
    const si = edge ? nodeIndex.get(edge.source) : undefined;
    const ti = edge ? nodeIndex.get(edge.target) : undefined;
    if (si === undefined || ti === undefined) {
      tourAnchor.visible = false;
      return;
    }
    // 빔과 같은 곡선(같은 clear)의 중점 — 화살촉·피킹이 쓰는 controlPoint 그대로
    controlPoint(live.pos, live.pos, CTRL, si * 3, ti * 3, CHORD_CLEAR[model.kind]);
    bezier(live.pos, live.pos, CTRL, 0.5, P, si * 3, ti * 3);
    const c = project(P);
    const a = project(Q.fromArray(live.pos, si * 3));
    const ax = a.x;
    const ay = a.y;
    const b = project(Q.fromArray(live.pos, ti * 3));
    const len = Math.hypot(b.x - ax, b.y - ay);
    write(c.x, c.y, Math.min(150, Math.max(56, len * 0.28)), c.front);
  });

  return null;
}

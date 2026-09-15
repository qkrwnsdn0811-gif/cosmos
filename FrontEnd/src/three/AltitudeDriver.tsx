import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import type { SceneModel } from "@/lib/graph";
import { useGalaxy } from "@/store/galaxy";
import { ALT_FADE, ALT_MOVE, ALT_TOTAL, altitudeState, targetY } from "./altitude";
import { dragState, liveFor, morphState } from "./morph";

interface Props {
  model: SceneModel;
}

/** 목표에 도달했다고 보는 높이 차 — 이보다 작으면 전이를 시작하지 않는다 (기간만 바꿨는데 값이 같은 경우) */
const EPS = 0.01;
const easeInOut = (x: number) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);

/**
 * 간선·화살촉이 새 높이를 한 번 따라오게 한다 — 드래그와 같은 경로(dirty + version)라 인접 간선만 다시 굽는다.
 * dirty 는 소비자가 여럿이라 "새 변화를 알릴 때" 만 비운다 (morph.ts 주석과 같은 규칙).
 */
function markDirty(model: SceneModel) {
  dragState.dirty.clear();
  model.nodes.forEach((nd) => dragState.dirty.add(nd.id));
  dragState.version += 1;
}

/**
 * 주가 고도 구동기 — 스토어의 altitudeOn·altitudeWindow 를 보고 행성 y 를 목표 높이로 옮긴다 (은하 뷰 전용).
 *
 * 성능이 이 컴포넌트의 설계를 정한다: 행성이 움직이는 동안 간선을 매 프레임 다시 구우면 은하 전체가 멈춘다.
 * 그래서 전이를 "간선 페이드 아웃 → (간선이 숨은 동안) y 이동 → 인접 간선 1회 재생성 → 리빌" 로 나눈다
 * (구간 길이는 altitude.ts, 게이트 합성은 RelationLines·EdgeArrows).
 *
 * live.pos 의 y 만 건드리고 x·z 와 node.pos 는 그대로 두므로, 끌어 옮긴 행성도 자기 자리에서 높이만 바뀐다.
 */
export default function AltitudeDriver({ model }: Props) {
  const indexOf = useMemo(() => {
    const m = new Map<string, number>();
    model.nodes.forEach((nd, i) => m.set(nd.id, i));
    return m;
  }, [model]);
  /**
   * 프레임 루프만 읽고 쓰는 상태 (Director 의 rig 와 같은 패턴).
   * applied 는 지금 live.y 가 반영하고 있는 목표("off" | "on:1M" …)이고, 빈 문자열이면 아직 한 번도 안 썼다는 뜻이라
   * 다음 프레임에 전이 없이 즉시 적용한다 (최초 마운트·모델 교체).
   */
  const rig = useRef({
    model: null as SceneModel | null,
    applied: "",
    from: new Float32Array(0),
    to: new Float32Array(0),
    /** 이동 중 사용자가 끌기 시작한 노드 — 손을 따르게 두고 전이에서 뺀다 */
    skip: new Set<number>(),
    lastVersion: dragState.version,
    rebuilt: false,
  }).current;

  // 전이 도중에 은하를 떠나면(워프·라우트 이동) 게이트가 0 인 채로 굳어 기업 중심 뷰의 간선이 사라진다
  useEffect(
    () => () => {
      altitudeState.animating = false;
      altitudeState.t = 0;
    },
    [],
  );

  useFrame((_, delta) => {
    const dt = Math.min(delta, 0.05);
    const s = useGalaxy.getState();
    // live 는 렌더가 아니라 프레임에서 잡는다 — 렌더 스코프의 값을 프레임에서 고치면 react-hooks/immutability 에 걸린다 (WeakMap 조회라 비용은 없다)
    const live = liveFor(model);
    // 필터로 은하 모델이 교체돼도 keyed group 은 같아 이 컴포넌트는 살아남는다 — 버퍼와 진행 중인 전이를 새 모델에 맞춰 되돌린다
    if (rig.model !== model) {
      rig.model = model;
      rig.applied = "";
      rig.from = new Float32Array(model.nodes.length);
      rig.to = new Float32Array(model.nodes.length);
      rig.skip.clear();
      rig.rebuilt = false;
      altitudeState.animating = false;
      altitudeState.t = 0;
    }
    // 워프 낙하 중에는 sampleMorph 가 live 의 주인이다 — 여기서 y 를 쓰면 서로 덮어쓰고, 착지 뒤 목표가 반영되지 않는다.
    // applied 를 비운 채로 빠져나가므로 착지한 다음 프레임에 즉시 적용된다 (은하로 돌아온 경우)
    if (morphState.plan) {
      rig.lastVersion = dragState.version;
      return;
    }
    const n = model.nodes.length;
    const key = s.altitudeOn ? `on:${s.altitudeWindow}` : "off";
    const yFor = (i: number) => (s.altitudeOn ? targetY(model.nodes[i], s.altitudeWindow) : model.nodes[i].pos[1]);
    // 다른 주인이 live 를 움직이는 중이면(드래그·관성 글라이드·워프 모프) 끝난 뒤에 시작한다.
    // 글라이드는 프레임마다 dragState.version 을 올리므로 버전 변화가 곧 "누군가 옮기는 중" 이다
    const busy = dragState.id !== null || dragState.version !== rig.lastVersion || morphState.plan !== null;
    rig.lastVersion = dragState.version;

    if (!altitudeState.animating && key !== rig.applied) {
      if (!rig.applied) {
        // 최초 적용 — 전이 없이 목표 높이에서 팝인한다
        let moved = false;
        for (let i = 0; i < n; i += 1) {
          const y = yFor(i);
          if (Math.abs(y - live.pos[i * 3 + 1]) > EPS) moved = true;
          live.pos[i * 3 + 1] = y;
        }
        rig.applied = key;
        if (moved) markDirty(model);
      } else if (!busy) {
        let moved = false;
        for (let i = 0; i < n; i += 1) {
          rig.from[i] = live.pos[i * 3 + 1];
          rig.to[i] = yFor(i);
          if (Math.abs(rig.to[i] - rig.from[i]) > EPS) moved = true;
        }
        rig.applied = key;
        // 높이가 그대로면(등락률이 없는 우주, 값이 같은 다른 기간) 간선을 굳이 껐다 켜지 않는다
        if (moved) {
          rig.skip.clear();
          rig.rebuilt = false;
          altitudeState.animating = true;
          altitudeState.t = 0;
          altitudeState.version += 1;
        }
      }
    }

    if (!altitudeState.animating) return;
    altitudeState.t += dt;
    if (dragState.id) {
      const di = indexOf.get(dragState.id);
      if (di !== undefined) rig.skip.add(di);
    }
    const move = altitudeState.t - ALT_FADE;
    if (move > 0) {
      const k = easeInOut(Math.min(1, move / ALT_MOVE));
      for (let i = 0; i < n; i += 1) {
        if (rig.skip.has(i)) continue;
        live.pos[i * 3 + 1] = rig.from[i] + (rig.to[i] - rig.from[i]) * k;
      }
    }
    // 이동이 끝난 프레임에 딱 한 번 — 여기서만 간선이 다시 구워진다
    if (!rig.rebuilt && move >= ALT_MOVE) {
      rig.rebuilt = true;
      markDirty(model);
    }
    if (altitudeState.t >= ALT_TOTAL) {
      altitudeState.animating = false;
      altitudeState.t = 0;
    }
  });

  return null;
}

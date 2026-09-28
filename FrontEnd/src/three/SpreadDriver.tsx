import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import type { SceneModel } from "@/lib/graph";
import { spreadTargets } from "@/lib/spread";
import { useGalaxy } from "@/store/galaxy";
import { altitudeState, targetY } from "./altitude";
import { dragState, liveFor, morphState } from "./morph";
import { SP_FADE, SP_MOVE, SP_TOTAL, spreadState } from "./spread";

interface Props {
  model: SceneModel;
}

/** 목표에 도달했다고 보는 차 — 이보다 작으면 전이를 시작하지 않는다 (AltitudeDriver 의 EPS 와 같은 기준) */
const EPS = 0.01;
const easeInOut = (x: number) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);

/**
 * 간선이 새 배치를 한 번 따라오게 한다 — AltitudeDriver 의 markDirty 와 같은 규칙(dirty 는 소비자가 여럿이라
 * "새 변화를 알릴 때" 만 비운다. morph.ts 주석과 같은 규칙이다).
 */
function markDirty(model: SceneModel) {
  dragState.dirty.clear();
  model.nodes.forEach((nd) => dragState.dirty.add(nd.id));
  dragState.version += 1;
}

/**
 * 펼치기(spread) 구동기 — 스토어의 spread(0~1) 를 보고 행성을 나선 → 평면 → 정렬 격자로 옮긴다 (은하 뷰 전용).
 * 목표 좌표 계산은 lib/spread.spreadTargets 가 순수 함수로 하고, 여기는 AltitudeDriver 와 같은 3구간 전이
 * (간선 페이드 아웃 → live.pos 이동 → 인접 간선 1회 재생성 → 리빌)로 옮기기만 한다.
 *
 * 고도와 다른 점: 슬라이더는 끌리는 동안 프레임마다 목표가 바뀐다. 그때마다 이전 전이가 끝나길 기다리면
 * 손이 움직이는 동안 행성이 자꾸 멈칫거리므로, 이미 전이 중인데 목표가 또 바뀌면 끝까지 기다리지 않고
 * 지금 좌표(from)에서 새 목표(to)로 바로 이어 붙인다 — 간선은 이미 숨어 있는 이동 구간이라 다시 굽지 않아도 된다.
 *
 * base(t=0 좌표)는 주가 고도가 켜져 있으면 그 높이를 포함한다 — 그래서 슬라이더가 0 으로 돌아오면
 * 지금 고도값 그대로 정확히 복귀한다(팝 없음). base 는 전이가 시작될 때만 다시 잰다.
 */
export default function SpreadDriver({ model }: Props) {
  const indexOf = useMemo(() => {
    const m = new Map<string, number>();
    model.nodes.forEach((nd, i) => m.set(nd.id, i));
    return m;
  }, [model]);

  /** 프레임 루프만 읽고 쓰는 상태 (AltitudeDriver 의 rig 와 같은 패턴) */
  const rig = useRef({
    model: null as SceneModel | null,
    /** live 가 지금 반영하고 있는 슬라이더 값. NaN = 아직 한 번도 안 써서 다음 프레임에 전이 없이 적용한다 */
    applied: Number.NaN,
    from: new Float32Array(0),
    to: new Float32Array(0),
    /** t=0(나선) 좌표 — node.pos + (켜져 있으면) 고도 오프셋. 전이가 시작될 때만 다시 잰다 */
    base: new Float32Array(0),
    /** 이동 중 사용자가 끌기 시작한 노드 — 손을 따르게 두고 전이에서 뺀다 */
    skip: new Set<number>(),
    lastVersion: dragState.version,
    rebuilt: false,
  }).current;

  // 전이 도중에 은하를 떠나면(워프·라우트 이동) 게이트가 0 인 채로 굳어 간선이 숨은 채 남는다
  useEffect(
    () => () => {
      spreadState.animating = false;
      spreadState.t = 0;
      spreadState.applied = Number.NaN;
    },
    [],
  );

  useFrame((_, delta) => {
    const dt = Math.min(delta, 0.05);
    const s = useGalaxy.getState();
    // live 는 렌더가 아니라 프레임에서 잡는다 (WeakMap 조회라 비용은 없다) — AltitudeDriver 와 같은 이유
    const live = liveFor(model);

    // 필터로 은하 모델이 교체돼도 keyed group 은 같아 이 컴포넌트는 살아남는다 — 버퍼와 진행 중인 전이를 새 모델에 맞춰 되돌린다
    if (rig.model !== model) {
      rig.model = model;
      rig.from = new Float32Array(model.nodes.length * 3);
      rig.to = new Float32Array(model.nodes.length * 3);
      rig.base = new Float32Array(model.nodes.length * 3);
      rig.skip.clear();
      rig.rebuilt = false;
      rig.applied = Number.NaN;
      spreadState.animating = false;
      spreadState.t = 0;
      spreadState.applied = Number.NaN;
    }

    // 워프 낙하 중에는 sampleMorph 가 live 의 주인이다 — 여기서 건드리면 서로 덮어쓴다.
    // applied 를 비우지 않은 채로 빠져나가므로, 착지한 다음 프레임에 지금 슬라이더 값과 비교해 필요하면 이어서 전이한다
    if (morphState.plan) {
      rig.lastVersion = dragState.version;
      spreadState.applied = rig.applied;
      return;
    }

    const n = model.nodes.length;
    const target = s.spread;
    // 다른 주인이 live 를 움직이는 중이면(드래그·관성 글라이드·워프 모프·고도 전이) 끝난 뒤에 새 전이를 시작한다.
    // 이미 전이 중인데 목표가 또 바뀐 경우(슬라이더를 계속 끄는 중)는 이 가드 없이 바로 이어 붙인다 — 아래 분기 참고
    const busy = dragState.id !== null || dragState.version !== rig.lastVersion || morphState.plan !== null || altitudeState.animating;
    rig.lastVersion = dragState.version;

    const computeBase = () => {
      for (let i = 0; i < n; i += 1) {
        const nd = model.nodes[i];
        rig.base[i * 3] = nd.pos[0];
        rig.base[i * 3 + 1] = s.altitudeOn ? targetY(nd, s.altitudeWindow) : nd.pos[1];
        rig.base[i * 3 + 2] = nd.pos[2];
      }
    };

    if (Number.isNaN(rig.applied)) {
      // 최초 적용 — 전이 없이 목표 배치에서 팝인한다
      computeBase();
      spreadTargets(model, target, rig.base, rig.to);
      let moved = false;
      for (let i = 0; i < n * 3; i += 1) {
        if (Math.abs(rig.to[i] - live.pos[i]) > EPS) moved = true;
        live.pos[i] = rig.to[i];
      }
      rig.applied = target;
      if (moved) {
        markDirty(model);
        rig.lastVersion = dragState.version;
      }
    } else if (target !== rig.applied) {
      if (spreadState.animating) {
        // 슬라이더가 끌리는 도중 — 끝나길 기다리지 않고 지금 좌표에서 새 목표로 바로 이어 붙인다.
        // 간선은 이미 숨어 있는 이동 구간이므로 t 를 SP_FADE 로 두어 페이드 단계를 다시 거치지 않는다
        computeBase();
        rig.from.set(live.pos);
        spreadTargets(model, target, rig.base, rig.to);
        rig.applied = target;
        spreadState.t = SP_FADE;
        rig.rebuilt = false;
        // 이어 붙일 때 skip 도 비운다 — 아래에서 지금 끌고 있는 노드만 다시 넣으므로, 손을 뗀 행성이 전이에서 영영 빠지지 않는다
        rig.skip.clear();
      } else if (!busy) {
        computeBase();
        rig.from.set(live.pos);
        spreadTargets(model, target, rig.base, rig.to);
        rig.applied = target;
        rig.skip.clear();
        rig.rebuilt = false;
        spreadState.animating = true;
        spreadState.t = 0;
        spreadState.version += 1;
      }
    }

    if (!spreadState.animating) {
      spreadState.applied = rig.applied;
      return;
    }

    spreadState.t += dt;
    if (dragState.id) {
      const di = indexOf.get(dragState.id);
      if (di !== undefined) rig.skip.add(di);
    }
    const move = spreadState.t - SP_FADE;
    if (move > 0) {
      const k = easeInOut(Math.min(1, move / SP_MOVE));
      for (let i = 0; i < n; i += 1) {
        if (rig.skip.has(i)) continue;
        const o = i * 3;
        live.pos[o] = rig.from[o] + (rig.to[o] - rig.from[o]) * k;
        live.pos[o + 1] = rig.from[o + 1] + (rig.to[o + 1] - rig.from[o + 1]) * k;
        live.pos[o + 2] = rig.from[o + 2] + (rig.to[o + 2] - rig.from[o + 2]) * k;
      }
    }
    // 이동이 끝난 프레임에 딱 한 번 — 여기서만 간선이 다시 구워진다
    if (!rig.rebuilt && move >= SP_MOVE) {
      rig.rebuilt = true;
      markDirty(model);
      rig.lastVersion = dragState.version;
    }
    if (spreadState.t >= SP_TOTAL) {
      spreadState.animating = false;
      spreadState.t = 0;
    }
    spreadState.applied = rig.applied;
  });

  return null;
}

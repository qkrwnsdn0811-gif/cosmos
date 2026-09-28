import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import { GALAXY_ELLIPSE_SQUASH, type SceneModel, type SceneNode } from "@/lib/graph";
import { RINGS_UI } from "@/lib/guide";
import { useGalaxy } from "@/store/galaxy";
import { morphState } from "./morph";
import { spreadDim } from "./spread";
import "./rings.css";

interface Props {
  model: SceneModel;
}

const SEGMENTS = 96;
/** 0선·기업 중심 궤도는 같은 옅은 청회색을 쓴다 — 색이 아니라 자리가 읽어야 하는 요소라서 */
const RING_COLOR = "#8FA6C8";
/**
 * 허브(연결 랭크) 링은 따로 — 관계선 색(파랑·보라·빨강 계열)과 헷갈리지 않도록 금색 계열로 구분한다.
 * RINGS_UI.hubPercents([10, 30, 60])와 순서를 맞춘 단일 색상(금색) 램프 — 순위(ordinal) 값이라
 * 밝기만 단조롭게 바뀐다: 상위 10%(가장 배타적)가 가장 밝고, 60%로 갈수록 어두워진다.
 * 이렇게 하면 사용자가 두 링(예: 10%와 30%) 사이에 있는 기업을 보고 "이 기업은 상위 10~30%구나"를
 * 색 띠(band)로 바로 읽을 수 있다 — 예전처럼 라벨 글자 색만 다르고 링 자체는 같은 색이면 안 된다.
 */
const HUB_RING_COLORS = ["#F4D9A0", "#D9B36C", "#A9824C"];
const HUB_OPACITY = 0.07;
const ZERO_OPACITY = 0.05;
const SYSTEM_OPACITY = 0.08;
const ZERO_FRACTIONS = [0.25, 0.5, 0.75, 1.0];
/** layoutSystem(graph.ts) 의 SYSTEM_SQUASH 와 같은 값 — 그 상수는 export 되지 않아 여기서 그대로 맞춘다 (바꾸면 궤도 링이 행성 궤도에서 어긋난다) */
const SYSTEM_SQUASH = 0.82;
/** 허브 링 라벨 각도 — "1시 방향" */
const HUB_LABEL_ANGLE = -Math.PI / 3;
/** 이 거리(원점까지) 이상에서만 허브 라벨이 보인다 */
const HUB_LABEL_FAR = 220;
/** 거리 페이드·리빌 감쇠의 시간 상수(초) */
const FADE_TAU = 0.4;
const REVEAL_TAU = 0.15;
/** 기업 중심 궤도 라벨을 타원 바깥으로 밀어내는 여유(월드) */
const DEPTH_LABEL_MARGIN = 4;

type MatEntry = { mat: THREE.LineBasicMaterial; base: number };

/**
 * 공간 참조 구조 — 은하 뷰의 허브 랭크 링·0선 기준면, 기업 중심 뷰의 depth 궤도 타원 (기획서 P0-6).
 * 자리 감각만 주는 배경 요소라 색은 전부 같은 옅은 청회색이고, 빔보다 먼저(뒤에) 그린다.
 */
export default function ReferenceRings({ model }: Props) {
  const groupRef = useRef<THREE.Group>(null);
  const altitudeOn = useGalaxy((s) => s.altitudeOn);
  const mats = useRef<Record<string, MatEntry>>({});
  const labels = useRef<Record<string, HTMLDivElement>>({});
  const fade = useRef(1);
  const reveal = useRef(1);

  const circleGeo = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const pos = new Float32Array(SEGMENTS * 3);
    for (let i = 0; i < SEGMENTS; i += 1) {
      const a = (i / SEGMENTS) * Math.PI * 2;
      pos[i * 3] = Math.cos(a);
      pos[i * 3 + 1] = 0;
      pos[i * 3 + 2] = Math.sin(a);
    }
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return geo;
  }, []);
  useEffect(() => () => circleGeo.dispose(), [circleGeo]);

  /**
   * 은하 뷰 — 허브 랭크 3링(연결 상위 10/30/60%) + 노드 높이 중앙값.
   * 랭크는 반지름이 아니라 실제 연결 가중치(weight, 없으면 degree)로 매긴다 — 반지름은 결과를 그리는 자리일 뿐이다.
   * 은하는 GALAXY_ELLIPSE_SQUASH 만큼 z 를 눌러 타원으로 배치하므로, 링 반지름은 layoutSystem 의 SYSTEM_SQUASH 패턴과
   * 같은 방식으로 한 번 편(un-squash) 뒤 다시 같은 비율로 눌러 그려야 행성 타원과 어긋나지 않는다.
   */
  const galaxyData = useMemo(() => {
    if (model.kind !== "galaxy" || !model.nodes.length) return null;
    const ranked = model.nodes.slice().sort((a, b) => (b.weight ?? b.degree) - (a.weight ?? a.degree));
    const n = ranked.length;
    const circR = (nd: SceneNode) => Math.hypot(nd.pos[0], nd.pos[2] / GALAXY_ELLIPSE_SQUASH);
    const hub = RINGS_UI.hubPercents.map((pct) => circR(ranked[Math.min(n - 1, Math.max(0, Math.round((pct / 100) * (n - 1))))]));
    const ys = model.nodes.map((nd) => nd.pos[1]).sort((a, b) => a - b);
    const midY = ys[Math.floor((ys.length - 1) / 2)];
    return { hub, midY, maxR: model.extent.maxR };
  }, [model]);

  /** 기업 중심 뷰 — depth 1·2·3 각각의 (눌림을 편 반지름) 중앙값. 그 depth 에 노드가 없으면 생략 */
  const systemRings = useMemo(() => {
    if (model.kind !== "system") return [];
    const byDepth = new Map<number, number[]>();
    model.nodes.forEach((nd) => {
      if (nd.depth < 1 || nd.depth > 3) return;
      const r = Math.hypot(nd.pos[0], nd.pos[2] / SYSTEM_SQUASH);
      if (!byDepth.has(nd.depth)) byDepth.set(nd.depth, []);
      byDepth.get(nd.depth)!.push(r);
    });
    const out: { depth: number; r: number }[] = [];
    for (let d = 1; d <= 3; d += 1) {
      const arr = byDepth.get(d);
      if (!arr?.length) continue;
      arr.sort((a, b) => a - b);
      out.push({ depth: d, r: arr[Math.floor((arr.length - 1) / 2)] });
    }
    return out;
  }, [model]);

  useFrame((state, delta) => {
    const g = groupRef.current;
    if (!g) return;
    const dt = Math.min(delta, 0.05);
    const warping = useGalaxy.getState().phase === "warp";
    g.visible = !warping;
    if (warping) {
      // drei Html 은 조상 Object3D.visible 을 보지 않는다 — 선은 사라졌는데 글자만 워프 연출 위에 남지 않게 직접 숨긴다
      Object.values(labels.current).forEach((el) => {
        el.style.opacity = "0";
      });
      return;
    }

    const revealTarget = morphState.plan ? 0 : morphState.reveal;
    reveal.current += (revealTarget - reveal.current) * (1 - Math.exp(-dt / REVEAL_TAU));
    // 펼치기 정렬 단계에서는 랭크 링·0선이 격자 위에서 의미가 없어져, 슬라이더를 따라 즉시 흐려진다(spreadDim)
    const dim = spreadDim();
    Object.values(mats.current).forEach(({ mat, base }) => {
      mat.opacity = base * reveal.current * dim;
    });

    const fadeTarget = state.camera.position.length() >= HUB_LABEL_FAR ? 1 : 0;
    fade.current += (fadeTarget - fade.current) * (1 - Math.exp(-dt / FADE_TAU));
    const hubAlpha = fade.current * reveal.current * dim;
    const alwaysAlpha = reveal.current;
    Object.entries(labels.current).forEach(([key, el]) => {
      el.style.opacity = String(key.startsWith("hub") ? hubAlpha : alwaysAlpha);
    });
  });

  return (
    <group ref={groupRef}>
      {galaxyData?.hub.map((r, i) => (
        <group key={`hub-${i}`} position={[0, galaxyData.midY, 0]}>
          <lineLoop
            geometry={circleGeo}
            scale={[r, 1, r * GALAXY_ELLIPSE_SQUASH]}
            frustumCulled={false}
            renderOrder={0}
            ref={(o) => {
              const key = `hub${i}`;
              if (o) mats.current[key] = { mat: o.material as THREE.LineBasicMaterial, base: HUB_OPACITY };
              else delete mats.current[key];
            }}
          >
            <lineBasicMaterial color={HUB_RING_COLORS[i]} transparent opacity={HUB_OPACITY} depthWrite={false} />
          </lineLoop>
          <Html position={[Math.cos(HUB_LABEL_ANGLE) * r, 0, Math.sin(HUB_LABEL_ANGLE) * r * GALAXY_ELLIPSE_SQUASH]} pointerEvents="none" zIndexRange={[3, 0]} className={`ring-label ring-label-hub ring-label-hub-${i}`}>
            <div
              ref={(el) => {
                const key = `hub${i}`;
                if (el) labels.current[key] = el;
                else delete labels.current[key];
              }}
            >
              {RINGS_UI.hub(RINGS_UI.hubPercents[i])}
            </div>
          </Html>
        </group>
      ))}
      {galaxyData && altitudeOn && (
        <group position={[0, galaxyData.midY, 0]}>
          {ZERO_FRACTIONS.map((f, i) => (
            <lineLoop
              key={`zero-${i}`}
              geometry={circleGeo}
              scale={[galaxyData.maxR * f, 1, galaxyData.maxR * f]}
              frustumCulled={false}
              renderOrder={0}
              ref={(o) => {
                const key = `zero${i}`;
                if (o) mats.current[key] = { mat: o.material as THREE.LineBasicMaterial, base: ZERO_OPACITY };
                else delete mats.current[key];
              }}
            >
              <lineBasicMaterial color={RING_COLOR} transparent opacity={ZERO_OPACITY} depthWrite={false} />
            </lineLoop>
          ))}
          <Html position={[galaxyData.maxR * 1.02, 0, 0]} pointerEvents="none" zIndexRange={[3, 0]} className="ring-label">
            <div
              ref={(el) => {
                if (el) labels.current.zero = el;
                else delete labels.current.zero;
              }}
            >
              {RINGS_UI.zeroPlane}
            </div>
          </Html>
        </group>
      )}
      {systemRings.map(({ depth, r }) => (
        <group key={`depth-${depth}`}>
          <lineLoop
            geometry={circleGeo}
            scale={[r, 1, r * SYSTEM_SQUASH]}
            frustumCulled={false}
            renderOrder={0}
            ref={(o) => {
              const key = `d${depth}`;
              if (o) mats.current[key] = { mat: o.material as THREE.LineBasicMaterial, base: SYSTEM_OPACITY };
              else delete mats.current[key];
            }}
          >
            <lineBasicMaterial color={RING_COLOR} transparent opacity={SYSTEM_OPACITY} depthWrite={false} />
          </lineLoop>
          <Html position={[0, 0, -(r * SYSTEM_SQUASH + DEPTH_LABEL_MARGIN)]} pointerEvents="none" zIndexRange={[3, 0]} className="ring-label">
            <div
              ref={(el) => {
                const key = `d${depth}`;
                if (el) labels.current[key] = el;
                else delete labels.current[key];
              }}
            >
              {RINGS_UI.depth[depth - 1]}
            </div>
          </Html>
        </group>
      ))}
    </group>
  );
}

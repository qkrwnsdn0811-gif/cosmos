import { useCallback, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { EventMap, Market } from "@/api/types";
import { capScale, layoutSystem, SYSTEM_SQUASH, type CompanyLookup } from "@/lib/graph";
import { RELATIONSHIP_META } from "@/lib/meta";
import { useLatestGraph } from "@/lib/queries";
import MiniScene from "@/three/MiniScene";
import { altitudeY } from "@/three/altitude";
import type { Emphasis } from "@/three/emphasis";
import { chipName, eventScene, MENTION, NEWS_NODE } from "./eventScene";

/** 기사 → 언급 기업 선 색 (관계 종류 색과 겹치지 않는 밝은 하늘색) */
const MENTION_COLOR = "#8FB2FF";
/** 판단 보류 기업의 밝기 배율 */
const HELD_DIM = 0.45;
/** 행성 크기 — 궤도가 넓은 이벤트(언급 5곳 + 2단계 추정)에서도 점으로 줄지 않게 은하 기업 중심 뷰보다 키운다 */
const SIZE_GAIN = 1.6;
const MIN_SIZE = { reported: 2.8, inferred: 1.9 };
const ACTIVE_TYPES = new Set(["SUPPLY", "INVEST", "PARTNER", "COMPETE", "CO_MENTIONED", MENTION]);
const LEGEND_TYPES = ["SUPPLY", "INVEST", "PARTNER", "COMPETE", "CO_MENTIONED"] as const;

/** 가운데 기사 별 — 빛나는 구와 옅은 광륜. 행성(CompanyNodes)이 아니라서 누를 수 없다 */
function NewsStar() {
  const core = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    core.current?.scale.setScalar(1 + Math.sin(clock.elapsedTime * 2) * 0.05);
  });
  return (
    <group>
      <mesh ref={core}>
        <sphereGeometry args={[4.2, 32, 32]} />
        <meshBasicMaterial color="#fff1c9" toneMapped={false} />
      </mesh>
      <mesh>
        <sphereGeometry args={[7.5, 32, 32]} />
        <meshBasicMaterial color="#ffcf6b" transparent opacity={0.12} depthWrite={false} />
      </mesh>
    </group>
  );
}

/** 궤도 안내선 — 단계마다 옅은 타원 하나 (배치와 같은 SYSTEM_SQUASH 비율) */
function OrbitRings({ radii }: { radii: number[] }) {
  const geometries = useMemo(
    () =>
      radii.map((r) => {
        const pts = Array.from({ length: 96 }, (_, i) => {
          const a = (i / 96) * Math.PI * 2;
          return new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r * SYSTEM_SQUASH);
        });
        return new THREE.BufferGeometry().setFromPoints(pts);
      }),
    [radii],
  );
  return (
    <group>
      {geometries.map((g, i) => (
        <lineLoop key={i} geometry={g}>
          <lineBasicMaterial color="#7d8cc4" transparent opacity={0.22} depthWrite={false} />
        </lineLoop>
      ))}
    </group>
  );
}

/**
 * 이벤트 관계 지도(3D) — 기사 1건이 하나의 태양계다. 가운데 별이 기사, 안쪽 궤도가 기사에 나온 기업, 바깥 궤도가
 * 공급망으로 추정한 기업(1단계·2단계)이다. 행성 높이는 사건 다음 거래일 초과수익률(위 상승·아래 하락, 관측값)이고,
 * 판단 보류 기업은 흐리게 그린다. 행성을 누르면 근거 패널이 그 기업으로 바뀌고, 다시 누르거나 Esc 를 누르면 풀린다.
 */
export default function EventGalaxy({
  map,
  selectedId,
  onSelect,
}: {
  map: EventMap;
  selectedId: string | null;
  onSelect: (companyId: string) => void;
}) {
  // 산업색·크기는 은하와 같은 척도로 — 전체 우주 스냅샷(은하 화면과 같은 캐시)에서 찾는다
  const universe = useLatestGraph(null);
  const lookup = useMemo<CompanyLookup>(() => {
    const nodes = universe.data?.nodes ?? [];
    const capT = capScale(nodes);
    const known = new Map(nodes.map((n) => [n.companyId, n]));
    const fromEvent = new Map(map.nodes.flatMap((n) => (n.companyId ? [[n.companyId, n] as const] : [])));
    return (id) => {
      const u = known.get(id);
      const e = fromEvent.get(id);
      if (!u && !e) return undefined;
      return {
        name: e?.name ?? u?.name,
        industryName: u?.industryName ?? null,
        market: (u?.market ?? e?.market ?? "") as Market,
        stockCode: u?.stockCode ?? e?.stockCode ?? null,
        capT: u ? capT(u.marketCapKrw) : undefined,
      };
    };
  }, [universe.data, map.nodes]);

  const scene = useMemo(() => eventScene(map.event.eventId, map.event.dateline, map.nodes), [map]);
  const { model, rings } = useMemo(() => {
    const m = layoutSystem(scene.graph, lookup);
    // 궤도 안내선 반지름 — 단계별 행성 거리의 평균 (고도를 더하기 전, 원반 위 거리)
    const byDepth = new Map<number, number[]>();
    m.nodes.forEach((n) => {
      if (n.depth > 0) byDepth.set(n.depth, [...(byDepth.get(n.depth) ?? []), Math.hypot(n.pos[0], n.pos[2] / SYSTEM_SQUASH)]);
    });
    const radii = [...byDepth.keys()].sort((a, b) => a - b).map((d) => byDepth.get(d)!.reduce((a, b) => a + b, 0) / byDepth.get(d)!.length);
    m.nodes.forEach((n) => {
      if (n.id === NEWS_NODE) return;
      const ar = scene.dayOne.get(n.id) ?? null;
      const reported = scene.reported.has(n.id);
      // 이 모델은 여기서 막 만든 것이라 좌표를 바로 고친다 — 간선 from/to 가 같은 배열을 가리켜 선도 함께 따라온다
      n.pos[1] += altitudeY(ar === null ? null : ar * 100);
      // 이름표(마우스를 올리거나 고른 행성에만 뜬다)에 1일 초과수익률을 붙인다 — name 은 로고 이니셜용으로 그대로 둔다
      n.label = chipName(n.name, ar);
      n.size = Math.max(n.size * SIZE_GAIN, reported ? MIN_SIZE.reported : MIN_SIZE.inferred);
      if (reported) n.tier = 0;
      // 은하는 바깥 궤도일수록 흐리게 그리지만(emphasis DEPTH_DIM), 이 지도에서 흐림은 '판단 보류' 만 뜻한다
      n.depth = 1;
    });
    m.edges.forEach((e) => {
      if (e.type === MENTION) e.color = MENTION_COLOR;
    });
    return { model: m, rings: radii };
  }, [scene, lookup]);

  const adjustEmphasis = useCallback(
    (e: Emphasis): Emphasis => {
      const node = new Map(e.node);
      scene.held.forEach((id) => node.set(id, (node.get(id) ?? 1) * HELD_DIM));
      node.set(NEWS_NODE, 0);
      const pinned = new Set(model.nodes.map((n) => n.id).filter((id) => id !== NEWS_NODE));
      const badges = new Set([...e.badges, ...scene.reported]);
      badges.delete(NEWS_NODE);
      return { node, edge: e.edge, pinned, badges, hidden: new Set([...e.hidden, NEWS_NODE]) };
    },
    [scene, model],
  );

  const companies = map.nodes.filter((n) => n.companyId);
  return (
    <div
      className="event-galaxy"
      onKeyDown={(e) => {
        if (e.key === "Escape" && selectedId) onSelect(selectedId);
      }}
    >
      <div className="event-galaxy-stage">
        <MiniScene
          model={model}
          sceneKey={map.event.eventId}
          activeTypes={ACTIVE_TYPES}
          adjustEmphasis={adjustEmphasis}
          highlightId={selectedId}
          onPickCompany={(id) => id !== NEWS_NODE && onSelect(id)}
        >
          <NewsStar />
          <OrbitRings radii={rings} />
        </MiniScene>
      </div>
      {/* 3D 행성은 키보드·보조기기로 고를 수 없어 같은 선택을 목록으로도 둔다 */}
      <ul className="sr-only" aria-label="이벤트 기업 목록">
        {companies.map((n) => (
          <li key={n.companyId}>
            <button type="button" aria-pressed={n.companyId === selectedId} onClick={() => onSelect(n.companyId!)}>
              {n.name} · {n.basis === "REPORTED" ? "기사에 나온 기업" : n.status === "HOLD" ? "판단 보류" : `${n.hop}단계 추정`}
            </button>
          </li>
        ))}
      </ul>
      <ul className="event-map-legend" aria-label="범례">
        <li>
          <i className="star" /> 가운데 별: 이 기사
        </li>
        <li>안쪽 궤도: 기사에 나온 기업 · 바깥 궤도: 공급망으로 추정한 기업(1·2단계)</li>
        <li>
          <i className="sw" style={{ borderTopColor: MENTION_COLOR }} /> 기사 언급
        </li>
        {LEGEND_TYPES.map((t) => (
          <li key={t}>
            <i className="sw" style={{ borderTopColor: RELATIONSHIP_META[t].color }} /> {RELATIONSHIP_META[t].label}
          </li>
        ))}
        <li>흐린 행성: 판단 보류</li>
        <li className="meta">행성 높이 · 이름표 숫자(행성에 마우스를 올리면 보임): 사건 다음 거래일 초과수익률(위 상승 · 아래 하락, 관측값)</li>
      </ul>
    </div>
  );
}

import { useMemo } from "react";
import { CompanyAvatar, EdgeGlyph } from "@/components/ui";
import { fmtScore } from "@/lib/format";
import type { SceneEdge, SceneModel } from "@/lib/graph";
import { relationshipMeta } from "@/lib/meta";
import { hoverPath } from "@/lib/path";
import { useGalaxy } from "@/store/galaxy";
import { beamColorFor } from "@/three/toon";

interface Props {
  /** 현재 뷰 모델 — 기업 중심 뷰면 systemModel, 은하 뷰면 galaxyModel. GalaxyPage 가 넘겨준다 (Canvas 와 결합도 0) */
  model: SceneModel | null;
}

/**
 * 상단 중앙 최단 경로 스트립 — Scene.tsx Content 가 3D 강조(emphasis.path)에 쓰는 hoverPath 를 그대로 호출해
 * 같은 경로를 DOM 으로 그린다. 기업 중심 뷰는 중심(depth≥2 호버) 기준, 은하 뷰는 미리보기로 고른 기업으로 핀한
 * 기업 기준이다. 핀만 있고 아직 유효한 호버가 없으면(경로를 시도하지 않으면) 핀을 뗄 수 있는 칩만 보여준다.
 */
export default function PathStrip({ model }: Props) {
  const focusId = useGalaxy((s) => s.focusId);
  const hoveredId = useGalaxy((s) => s.hoveredId);
  const selectedId = useGalaxy((s) => s.selectedId);
  const minScore = useGalaxy((s) => s.minScore);
  const activeTypes = useGalaxy((s) => s.activeTypes);

  const allowed = useMemo(() => (e: SceneEdge) => e.score >= minScore && activeTypes.has(e.type), [minScore, activeTypes]);
  // 경로 기준·게이트·계산은 Scene.tsx 3D 강조와 같은 hoverPath 를 쓴다 — 규칙이 갈라지면 씬과 스트립이 다른 경로를 보여준다
  const hovering = useMemo(() => hoverPath(model, hoveredId, selectedId, allowed), [model, hoveredId, selectedId, allowed]);
  const path = hovering?.path ?? null;

  // 기업 중심 뷰는 브레드크럼·1홉 요약이 위에 쌓여 더 내려야 한다 — 두 분기가 같은 오프셋 규칙을 쓰도록 한 곳에서 만든다
  const wrapClass = `hud hud-path ${focusId ? "with-crumb" : ""}`;

  if (!hovering) return null;

  return (
    <div className={wrapClass}>
      <div className="path-strip" role="status" aria-label="최단 경로">
        {path ? (
          <>
            {path.nodes.map((id, i) => {
              const node = model?.nodeById.get(id);
              if (!node) return null;
              const edge = i > 0 ? model?.edgeById.get(path.edges[i - 1]) : undefined;
              return (
                <span className="path-seg" key={id}>
                  {edge && (
                    <span className="path-edge" title={`${relationshipMeta(edge.type).label} · ${fmtScore(edge.score)}점`}>
                      {/* 글리프 색은 씬의 빔과 같은 규칙 — 경로를 보다가 3D 로 눈을 옮겨도 같은 색이 같은 관계를 가리킨다 */}
                      <EdgeGlyph directed={edge.directed} color={beamColorFor(edge.impact)} />
                      <b style={{ color: relationshipMeta(edge.type).color }}>{relationshipMeta(edge.type).label}</b>
                      <span className="num">{fmtScore(edge.score)}</span>
                    </span>
                  )}
                  <span className="path-node">
                    <CompanyAvatar name={node.name} stockCode={node.stockCode} industry={node.industry} size={20} />
                    {node.name}
                  </span>
                </span>
              );
            })}
            <span className="path-sum meta num">
              {path.edges.length}홉 · 최약 {fmtScore(path.bottleneck)}점
            </span>
          </>
        ) : (
          <span className="path-empty meta">연결 경로 없음 — 필터를 완화해 보세요</span>
        )}
      </div>
    </div>
  );
}

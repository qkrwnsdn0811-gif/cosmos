import { useMemo } from "react";
import { RoleBadge } from "@/components/ui";
import { fmtScore } from "@/lib/format";
import type { SceneModel } from "@/lib/graph";
import { relationshipMeta } from "@/lib/meta";
import { ROLE_META, roleCounts } from "@/lib/roles";
import { useGalaxy } from "@/store/galaxy";

interface Props {
  /** 기업 중심 뷰 모델 (중심이 focusId 인 것) */
  model: SceneModel | null;
}

/**
 * 기업 중심 뷰의 1홉 요약 스트립 — 중심 기업의 이웃을 역할(공급사·고객·투자자·피투자·협력·경쟁)별로 세어 칩으로 늘어놓는다.
 * 칩을 누르면 그 역할의 이웃·빔만 남고(roleFilter), 다시 누르거나 ESC 로 복원된다.
 * 개수는 필터·예산과 무관한 전체 incident 기준이고, 유형 필터에서 꺼진 역할은 흐리게 표시한다.
 */
export default function HopSummary({ model }: Props) {
  const focusId = useGalaxy((s) => s.focusId);
  const phase = useGalaxy((s) => s.phase);
  const roleFilter = useGalaxy((s) => s.roleFilter);
  const activeTypes = useGalaxy((s) => s.activeTypes);
  const setRoleFilter = useGalaxy((s) => s.setRoleFilter);

  const ready = Boolean(focusId) && phase === "system" && model !== null && model.centerId === focusId;
  const counts = useMemo(() => (ready && model && focusId ? roleCounts(model, focusId) : []), [ready, model, focusId]);
  if (!ready || !counts.length) return null;

  const total = counts.reduce((s, c) => s + c.count, 0);
  const avg = counts.reduce((s, c) => s + c.avg * c.count, 0) / Math.max(1, total);

  return (
    <div className="hud hud-hop" aria-label="1홉 관계 요약">
      <div className="hop-strip">
        {counts.map((c) => {
          const type = ROLE_META[c.role].type;
          const off = !activeTypes.has(type);
          return (
            <RoleBadge
              key={c.role}
              role={c.role}
              count={c.count}
              on={roleFilter === c.role}
              dimmed={off}
              title={off ? `${relationshipMeta(type).label} 유형이 필터에서 꺼져 있습니다` : `${ROLE_META[c.role].label} ${c.count}곳 · 평균 ${fmtScore(Math.round(c.avg))}점 · 누르면 이 역할만 남깁니다`}
              onClick={() => setRoleFilter(c.role)}
            />
          );
        })}
        <span className="hop-total meta num">
          전체 {total} · 평균 {fmtScore(Math.round(avg))}점
        </span>
      </div>
    </div>
  );
}

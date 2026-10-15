import { useMemo } from "react";
import { useElementWidth } from "@/components/PriceChart";
import type { EventMap, EventNode } from "@/api/types";
import { fmtPct, fmtTradingDate } from "@/lib/format";
import { nodeStyle } from "./nodeStyle";
import { labelSide, linkTargetId, radialLayout, shortName, viaCompanyId } from "./radialLayout";

const SIZE = 520;
const CENTER = SIZE / 2;
/** 좌우 이름표가 잘리지 않도록 배치 영역 밖에 둔 여백 */
const PAD_X = 80;
const PAD_Y = 16;
const VIEW_W = SIZE + 2 * PAD_X;
const MIN_HIT_RADIUS = 14;
/** 화면에 그려진 크기 기준 최소값(px) — 지도가 작게 줄어들어도 글자와 터치 영역은 이만큼은 유지한다 */
const MIN_LABEL_PX = 10;
const MIN_TAP_PX = 24;

type PlacedNode = EventNode & { companyId: string };

/** 선 색은 근거·추정·판단 보류 구분에만 쓴다(노드 원의 색과 크기는 nodeStyle 이 영향 모델 결과로 정한다) */
function tone(n: EventNode) {
  if (n.basis === "REPORTED") return "var(--primary)";
  return n.status === "HOLD" ? "var(--text-4)" : "var(--accent)";
}
function basisLabel(n: EventNode) {
  if (n.basis === "REPORTED") return "근거";
  return n.status === "HOLD" ? "판단 보류" : "추정";
}
function dayOneReturn(n: EventNode) {
  return n.abnormalReturns.find((r) => r.windowDays === 1)?.abnormalReturn ?? null;
}

/**
 * 뉴스 1건의 관계 지도 — 안쪽 원은 기사에 언급된 기업(실선, 근거), 바깥 원은 공급망을 타고 추정한 기업(점선).
 * 추정 노드는 퍼져 나온 기업과 점선으로 잇는다: 2단계 경로는 중간 기업이 지도에 있으면 그 기업과 잇는다.
 * 노드를 다시 누르거나 Esc 를 누르면 선택이 풀린다(onSelect 는 같은 id 로 토글).
 */
export default function EventRadialMap({
  map,
  selectedId,
  onSelect,
}: {
  map: EventMap;
  selectedId: string | null;
  onSelect: (companyId: string) => void;
}) {
  const nodes = useMemo(() => map.nodes.filter((n): n is PlacedNode => Boolean(n.companyId)), [map.nodes]);
  const placed = useMemo(() => {
    const layout = radialLayout(
      nodes.map((n) => ({
        id: n.companyId,
        basis: n.basis,
        anchorId: n.anchorCompanyId,
        viaId: viaCompanyId(n.path, n.anchorCompanyId),
      })),
      SIZE,
    );
    return new Map(layout.map((p) => [p.id, p]));
  }, [nodes]);
  const onMap = useMemo(() => new Set(placed.keys()), [placed]);
  const [wrapRef, width] = useElementWidth<HTMLDivElement>();
  const scale = width ? Math.min(width, 560) / VIEW_W : 1;
  const labelFont = Math.max(11, MIN_LABEL_PX / scale);
  const hitRadius = Math.max(MIN_HIT_RADIUS, MIN_TAP_PX / 2 / scale);
  // 크게 줄어든 화면(모바일)에서는 글자를 키우는 대신 언급 기업과 고른 기업의 이름만 보여준다
  const compact = scale < 0.75;
  const COMPACT_LABELS = 6;
  // 노드는 언급 기업이 점수순으로 먼저 온다 — 줄어든 화면에서는 상위 몇 개만 이름을 단다
  const reportedRank = useMemo(
    () => new Map(nodes.filter((n) => n.basis === "REPORTED").map((n, i) => [n.companyId, i])),
    [nodes],
  );

  return (
    <div
      ref={wrapRef}
      className="event-map"
      onKeyDown={(e) => {
        if (e.key === "Escape" && selectedId) onSelect(selectedId);
      }}
    >
      {/* role="img" 는 자식을 표현용으로 숨겨 노드 버튼이 보조기기에 안 보인다 — group 으로 둔다 */}
      <svg viewBox={`${-PAD_X} ${-PAD_Y} ${SIZE + 2 * PAD_X} ${SIZE + 2 * PAD_Y}`} role="group" aria-label="이벤트 관계 지도">
        <g className="event-map-links">
          {nodes.map((n) => {
            const p = placed.get(n.companyId)!;
            const viaId = viaCompanyId(n.path, n.anchorCompanyId);
            const targetId = linkTargetId({ basis: n.basis, hop: n.hop, anchorId: n.anchorCompanyId, viaId }, onMap);
            const to = targetId ? placed.get(targetId) : undefined;
            return (
              <line
                key={`l-${n.companyId}`}
                x1={p.x}
                y1={p.y}
                x2={to ? to.x : CENTER}
                y2={to ? to.y : CENTER}
                stroke={tone(n)}
                strokeOpacity={n.status === "HOLD" ? 0.45 : 0.75}
                strokeWidth={n.basis === "REPORTED" ? 2 : 1.4}
                strokeDasharray={n.basis === "INFERRED" ? "5 4" : undefined}
              />
            );
          })}
        </g>

        <g className="event-map-center">
          <circle cx={CENTER} cy={CENTER} r={34} fill="var(--panel-solid)" stroke="var(--border-strong)" />
          <text x={CENTER} y={CENTER - 3} textAnchor="middle" className="event-map-center-label">
            뉴스 이벤트
          </text>
          <text x={CENTER} y={CENTER + 13} textAnchor="middle" className="event-map-center-date">
            {map.event.tradingDate ? fmtTradingDate(map.event.tradingDate) : "-"}
          </text>
        </g>

        {nodes.map((n) => {
          const p = placed.get(n.companyId)!;
          const style = nodeStyle(n);
          const r = style.radius;
          const label = `${n.name} ${basisLabel(n)}${style.impactLabel ? ` · ${style.impactLabel}` : ""}`;
          const ar = dayOneReturn(n);
          const selected = n.companyId === selectedId;
          const side = labelSide(p.x, SIZE);
          const lx = side === "start" ? p.x + r + 6 : side === "end" ? p.x - r - 6 : p.x;
          const ly = side === "middle" ? p.y + r + labelFont * 1.15 : p.y + labelFont * 0.35;
          const showLabel = !compact || selected || (reportedRank.get(n.companyId) ?? Infinity) < COMPACT_LABELS;
          return (
            <g
              key={n.companyId}
              className={`event-map-node ${selected ? "on" : ""} ${n.status === "HOLD" ? "hold" : ""}`}
              role="button"
              tabIndex={0}
              aria-label={label}
              aria-pressed={selected}
              onClick={() => onSelect(n.companyId)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(n.companyId);
                }
              }}
            >
              <title>{label}</title>
              {/* 작은 노드도 누를 수 있도록 투명한 터치 영역 */}
              <circle cx={p.x} cy={p.y} r={Math.max(r, hitRadius)} className="event-map-hit" />
              <circle
                cx={p.x}
                cy={p.y}
                r={r}
                fill={style.fill}
                fillOpacity={style.fillOpacity}
                stroke={style.stroke ?? "transparent"}
                strokeWidth={2.5}
              />
              {/* 선택 표시는 바깥에 따로 그려 속 빈 원(방향 보류)의 테두리와 겹치지 않게 한다 */}
              {selected && (
                <circle cx={p.x} cy={p.y} r={r + 4} fill="none" stroke="var(--text)" strokeWidth={2} className="event-map-ring" />
              )}
              <g className="event-map-label" style={{ display: showLabel ? undefined : "none" }}>
                <text x={lx} y={ly} textAnchor={side} className="event-map-name" style={{ fontSize: labelFont }}>
                  {shortName(n.name)}
                </text>
                {ar !== null && (
                  <text
                    x={lx}
                    y={ly + labelFont * 1.15}
                    textAnchor={side}
                    className="event-map-ar"
                    style={{ fontSize: labelFont * 0.95 }}
                    fill={ar > 0 ? "var(--pos)" : ar < 0 ? "var(--neg)" : "var(--text-3)"}
                  >
                    {fmtPct(ar * 100, 1)}
                  </text>
                )}
              </g>
            </g>
          );
        })}
      </svg>

      <ul className="event-map-legend" aria-label="범례">
        <li>
          <i className="sw solid" /> 실선 근거(기사 언급)
        </li>
        <li>
          <i className="sw dashed" /> 점선 추정(공급망 전이)
        </li>
        <li>
          <i className="sw hold" /> 회색 판단 보류
        </li>
        <li>
          <i className="dot pos" /> 상승
        </li>
        <li>
          <i className="dot neg" /> 하락
        </li>
        <li>
          <i className="dot neu" /> 중립
        </li>
        <li>
          <i className="dot hollow" /> 속 빈 원 방향 보류
        </li>
        <li className="meta">색 없는 노드: 영향 모델 미적용(해외 상장·판단 보류)</li>
        <li className="meta">숫자: 관측된 1일 초과수익률</li>
      </ul>
    </div>
  );
}

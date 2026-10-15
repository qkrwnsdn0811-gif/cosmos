import { useMemo } from "react";
import type { EventMap, EventNode } from "@/api/types";
import { fmtPct, fmtTradingDate } from "@/lib/format";
import { radialLayout } from "./radialLayout";

const SIZE = 520;
const CENTER = SIZE / 2;

/** 1단계에는 영향 방향 모델이 없다 — 색은 근거·추정·판단 보류 구분에만 쓴다 */
function nodeFill(n: EventNode) {
  if (n.basis === "REPORTED") return "var(--primary)";
  return n.status === "HOLD" ? "var(--text-4)" : "var(--accent)";
}
function nodeRadius(n: EventNode) {
  const s = n.score ?? 0.5;
  return n.basis === "REPORTED" ? 10 + 8 * s : 8 + 10 * s;
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
 * 추정 노드는 자신이 퍼져 나온 언급 기업과 점선으로 잇는다. 노드 아래 숫자는 관측된 1일 초과수익률이다.
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
  const nodes = useMemo(() => map.nodes.filter((n): n is EventNode & { companyId: string } => Boolean(n.companyId)), [map.nodes]);
  const placed = useMemo(() => {
    const layout = radialLayout(
      nodes.map((n) => ({ id: n.companyId, basis: n.basis, anchorId: n.anchorCompanyId })),
      SIZE,
    );
    return new Map(layout.map((p) => [p.id, p]));
  }, [nodes]);

  return (
    <div className="event-map">
      {/* role="img" 는 자식을 표현용으로 숨겨 노드 버튼이 보조기기에 안 보인다 — group 으로 둔다 */}
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="group" aria-label="이벤트 관계 지도">
        <g className="event-map-links">
          {nodes.map((n) => {
            const p = placed.get(n.companyId)!;
            const to = n.basis === "INFERRED" && n.anchorCompanyId ? placed.get(n.anchorCompanyId) : undefined;
            const x2 = to ? to.x : CENTER;
            const y2 = to ? to.y : CENTER;
            return (
              <line
                key={`l-${n.companyId}`}
                x1={p.x}
                y1={p.y}
                x2={x2}
                y2={y2}
                stroke={n.basis === "REPORTED" ? "var(--primary)" : n.status === "HOLD" ? "var(--text-4)" : "var(--accent)"}
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
          const r = nodeRadius(n);
          const ar = dayOneReturn(n);
          const selected = n.companyId === selectedId;
          return (
            <g
              key={n.companyId}
              className={`event-map-node ${selected ? "on" : ""}`}
              role="button"
              tabIndex={0}
              aria-label={`${n.name} ${basisLabel(n)}`}
              aria-pressed={selected}
              onClick={() => onSelect(n.companyId)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(n.companyId);
                }
              }}
            >
              <circle
                cx={p.x}
                cy={p.y}
                r={r}
                fill={nodeFill(n)}
                fillOpacity={n.status === "HOLD" ? 0.6 : 1}
                stroke={selected ? "var(--text)" : "transparent"}
                strokeWidth={2.5}
              />
              <text x={p.x} y={p.y + r + 13} textAnchor="middle" className="event-map-name">
                {n.name}
              </text>
              {ar !== null && (
                <text
                  x={p.x}
                  y={p.y + r + 26}
                  textAnchor="middle"
                  className="event-map-ar"
                  fill={ar > 0 ? "var(--pos)" : ar < 0 ? "var(--neg)" : "var(--text-3)"}
                >
                  {fmtPct(ar * 100, 1)}
                </text>
              )}
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
        <li className="meta">숫자: 관측된 1일 초과수익률</li>
      </ul>
    </div>
  );
}

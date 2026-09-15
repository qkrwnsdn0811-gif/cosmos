import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ImpactDirection, Market, RelationshipType, Sentiment } from "@/api/types";
import { IMPACT_META, RELATIONSHIP_ORDER, SENTIMENT_META, industryColor, relationshipMeta } from "@/lib/meta";
import { initials } from "@/lib/format";
import { logoUrl } from "@/lib/logos";
import { ROLE_META, roleColor, type RoleKey } from "@/lib/roles";
import { LAB } from "@/three/toon";

/* ------------------------------ 로고 아바타 ------------------------------ */

export function CompanyAvatar({ name, stockCode, industry, size = 44, radius }: { name: string; stockCode?: string | null; industry?: string | null; size?: number; radius?: number }) {
  const [broken, setBroken] = useState(false);
  const url = logoUrl(stockCode);
  const hasLogo = Boolean(url) && !broken;
  const color = industryColor(industry);
  return (
    <span
      className="avatar"
      style={{ width: size, height: size, borderRadius: radius ?? Math.round(size * 0.28), fontSize: Math.max(11, Math.round(size * 0.3)), background: hasLogo ? "#fff" : `linear-gradient(140deg, ${color}66, ${color}22)`, color: "#fff", border: `1px solid ${color}55` }}
      aria-hidden="true"
    >
      {hasLogo ? <img src={url!} alt="" onError={() => setBroken(true)} /> : initials(name)}
    </span>
  );
}

/* ------------------------------ 배지 ------------------------------ */
export function SentimentBadge({ value, compact = false }: { value: Sentiment; compact?: boolean }) {
  const m = SENTIMENT_META[value] ?? SENTIMENT_META.NEUTRAL;
  return (
    <span className="badge" style={{ background: m.bg, color: m.fg }}>
      <i className="dot" style={{ background: m.color }} />
      {compact ? "" : m.label}
    </span>
  );
}
/** impactDirection 은 선택 필드(NULL 허용)라 값이 없으면 중립으로 읽는다. */
export function ImpactBadge({ value }: { value: ImpactDirection | null | undefined }) {
  const m = (value && IMPACT_META[value]) ?? IMPACT_META.NEUTRAL;
  return (
    <span className="badge" style={{ background: m.bg, color: m.fg }}>
      <i className="dot" style={{ background: m.color }} />
      {m.label}
    </span>
  );
}
export function TypeBadge({ type, directed }: { type: RelationshipType; directed?: boolean }) {
  const m = relationshipMeta(type);
  return (
    <span className="badge" style={{ background: `${m.color}22`, color: m.color }}>
      <i className="dot" style={{ background: m.color }} />
      {m.label}
      {directed !== undefined && <span style={{ opacity: 0.7 }}>{directed ? "→" : "↔"}</span>}
    </span>
  );
}
export function IndustryBadge({ name }: { name: string }) {
  const c = industryColor(name);
  return (
    <span className="badge" style={{ background: `${c}22`, color: c }}>
      {name}
    </span>
  );
}
/**
 * 관계 역할 칩 — 유형색 점 + 역할 라벨 + 개수. 1홉 요약 스트립에서 누르면 그 역할만 남긴다(on).
 * 유형 필터에서 꺼진 역할은 흐리게 두고 title 로 이유를 알린다
 */
export function RoleBadge({ role, count, on = false, dimmed = false, title, onClick }: { role: RoleKey; count: number; on?: boolean; dimmed?: boolean; title?: string; onClick?: () => void }) {
  const color = roleColor(role);
  return (
    <button type="button" className={`chip chip-sm role-badge ${on ? "on" : ""}`} onClick={onClick} title={title} aria-pressed={on} style={{ opacity: dimmed ? 0.4 : 1, borderColor: on ? `${color}99` : undefined, background: on ? `${color}22` : undefined }}>
      <i className="dot" style={{ background: color }} />
      {ROLE_META[role].label}
      <b className="num">{count}</b>
    </button>
  );
}
export function MarketTag({ market }: { market: Market }) {
  return (
    <span className="meta num" style={{ fontFamily: "var(--mono)", fontSize: 11, letterSpacing: "0.04em" }}>
      {market}
    </span>
  );
}

/* ------------------------------ 점수 ------------------------------ */
export function ScoreBar({ score, color }: { score: number; color?: string }) {
  return (
    <span className="bar" style={{ flex: 1 }}>
      <i style={{ width: `${Math.max(2, Math.min(100, score))}%`, background: color ? `linear-gradient(90deg, ${color}99, ${color})` : undefined }} />
    </span>
  );
}

/* ------------------------------ 관계 유형 범례 ------------------------------ */
/**
 * 간선의 방향을 선 모양으로 나타낸다: 방향 있음 = 실선 + 화살, 방향 없음 = 점선.
 * 빔 색은 영향 방향이 정하므로(긍정 파랑·부정 빨강·중립 보라) 실제 간선을 가리킬 때는 color 로 그 색을 넘긴다.
 * 특정 간선이 아닌 자리(유형 범례)는 기본값인 중립 보라로 둬서 색이 방향을 잘못 주장하지 않게 한다.
 * 유형색은 빔이 아니라 기업을 호버했을 때 이웃 행성의 링·칩 태그에 나타나므로, 범례 칩 앞의 색 점이 그 대응을 알려 준다
 * PathStrip(최단 경로 스트립)도 같은 글리프로 홉 사이 방향을 나타내므로 export 한다.
 */
export function EdgeGlyph({ directed, color = LAB.beamNeutral }: { directed: boolean; color?: string }) {
  return (
    <svg className="edge-glyph" width="22" height="10" viewBox="0 0 22 10" aria-hidden>
      <line x1="1" y1="5" x2={directed ? 15 : 21} y2="5" stroke={color} strokeWidth="2" strokeLinecap="round" strokeDasharray={directed ? undefined : "2.5 3"} />
      {directed && <path d="M14 1.5 L19.5 5 L14 8.5" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />}
    </svg>
  );
}

export function TypeLegend({ active, onToggle }: { active?: Set<RelationshipType>; onToggle?: (t: RelationshipType) => void }) {
  return (
    <div className="row wrap" style={{ gap: 8 }}>
      {RELATIONSHIP_ORDER.map((t) => {
        const m = relationshipMeta(t);
        const on = active ? active.has(t) : true;
        return (
          <button key={t} type="button" className={`chip chip-sm ${on ? "on" : ""}`} onClick={() => onToggle?.(t)} title={`${m.description} · ${m.directed ? "방향 있음 (펄스가 한쪽으로 흐름)" : "방향 없음 (점선)"} · 기업을 호버하면 이 색 링으로 표시`} style={{ opacity: on ? 1 : 0.55 }}>
            <i className="dot" style={{ background: m.color }} />
            <EdgeGlyph directed={m.directed} />
            {m.label}
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------ 상태 표시 ------------------------------ */
export function Skeleton({ h = 14, w = "100%", style }: { h?: number; w?: number | string; style?: React.CSSProperties }) {
  return <div className="skeleton" style={{ height: h, width: w, ...style }} />;
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
/** onClick 을 주면 이 수치가 어떻게 나왔는지 보여주는 근거 화면 등을 열 수 있도록 button 으로 렌더링한다. */
export function Kpi({ label, value, unit, tone, children, onClick }: { label: string; value: ReactNode; unit?: string; tone?: string; children?: ReactNode; onClick?: () => void }) {
  const body = (
    <>
      <div className="lab">
        {label}
        {/* 클릭 가능한 Kpi 는 hover 전에도 '눌러볼 수 있다'는 게 드러나야 하므로 화살표를 덧붙인다 */}
        {onClick && (
          <span className="kpi-hint" aria-hidden="true">
            {" "}
            ▸
          </span>
        )}
      </div>
      <div className="kpi-v num" style={{ color: tone }}>
        {value}
        {unit && <span className="lab" style={{ marginLeft: 4 }}>{unit}</span>}
      </div>
      {children}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className="card kpi kpi-click" onClick={onClick}>
        {body}
      </button>
    );
  }
  return <div className="card kpi">{body}</div>;
}

/* ------------------------------ 외부 클릭 감지 ------------------------------ */
export function useClickOutside<T extends HTMLElement>(onOutside: () => void, active = true) {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (!active) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onOutside();
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [onOutside, active]);
  return ref;
}

/* ------------------------------ 아이콘 ------------------------------ */
export const Icon = {
  Search: () => (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="M16.5 16.5 21 21" />
    </svg>
  ),
  Refresh: () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M20 12a8 8 0 1 1-2.34-5.66" />
      <path d="M20 4v5h-5" />
    </svg>
  ),
  Close: () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  ),
  Chevron: () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">
      <path d="M6 9l6 6 6-6" />
    </svg>
  ),
  Back: () => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
      <path d="M15 6l-6 6 6 6" />
    </svg>
  ),
  Star: ({ filled }: { filled?: boolean }) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M12 3l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.9 6.1 21.1l1.3-6.6L2.5 9.9l6.6-.8z" />
    </svg>
  ),
  Bookmark: ({ filled }: { filled?: boolean }) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M6 3h12v18l-6-4-6 4z" />
    </svg>
  ),
  External: () => (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
      <path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
    </svg>
  ),
  Galaxy: () => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="2.5" />
      <path d="M12 3a9 9 0 0 1 9 9M12 21a9 9 0 0 1-9-9" />
      <path d="M4.5 6.5a9 9 0 0 1 4-2.6M19.5 17.5a9 9 0 0 1-4 2.6" />
    </svg>
  ),
  Eye: () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z" />
      <circle cx="12" cy="12" r="2.6" />
    </svg>
  ),
  EyeOff: () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M10.6 6.7A9.6 9.6 0 0 1 12 5.5c6.4 0 10 6.5 10 6.5a17 17 0 0 1-3.4 4.1M6.3 8.1A17.6 17.6 0 0 0 2 12s3.6 6.5 10 6.5a9.9 9.9 0 0 0 3.5-.6" />
      <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
      <path d="M3 3l18 18" />
    </svg>
  ),
  Help: () => (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.3a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2.1-2.5 3.8" />
      <path d="M12 17.2h.01" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  ),
  /* 카메라 프리셋 — 궤도(비스듬히 도는 궤도), 위에서(원반을 정확히 위에서), 정면에서(원반을 옆에서 본 선) */
  CamOrbit: () => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <ellipse cx="12" cy="12" rx="9" ry="4.2" transform="rotate(-20 12 12)" />
      <circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none" />
    </svg>
  ),
  CamTop: () => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
    </svg>
  ),
  CamFront: () => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M3 13.5h18" />
      <ellipse cx="12" cy="13.5" rx="8" ry="1.6" />
      <path d="M8 13.5V8.5M12 13.5V6M16 13.5v-4" strokeLinecap="round" />
    </svg>
  ),
  Cursor: () => (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" stroke="#0b1020" strokeWidth="1.4" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 3l12 9.2-5.4.9 3.1 5.9-2.6 1.3-3.1-6-3.8 3.9z" />
    </svg>
  ),
};

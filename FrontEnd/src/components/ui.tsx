import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import type { ImpactDirection, Market, RelationshipType, Sentiment } from "@/api/types";
import { IMPACT_META, RELATIONSHIP_ORDER, SENTIMENT_META, UNCLASSIFIED_INDUSTRY, industryColor, relationshipMeta } from "@/lib/meta";
import { initials } from "@/lib/format";
import { logoUrl, useCompanyLogoRef } from "@/lib/logos";
import { ROLE_META, roleColor, type RoleKey } from "@/lib/roles";
import { LAB } from "@/three/palette";

/* ------------------------------ 로고 아바타 ------------------------------ */

/**
 * 기업 로고 — 로고 키는 `시장 + 종목코드`다. 두 값을 아는 화면은 그대로 넘기고,
 * `companyId`·`name` 만 가진 화면(뉴스의 관련 기업 등)은 companyId 를 넘기면 api 경계에서 적어 둔 참조로 찾는다.
 * 파일이 없거나 이미지 로딩이 실패하면 깨진 이미지 대신 기업명 이니셜을 그린다.
 */
export function CompanyAvatar({
  name,
  companyId,
  stockCode,
  market,
  industry,
  size = 44,
  radius,
}: {
  name: string;
  companyId?: string | null;
  stockCode?: string | null;
  market?: Market | null;
  industry?: string | null;
  size?: number;
  radius?: number;
}) {
  // 이미 코드를 아는 화면은 조회를 건너뛴다 — 훅 자체는 항상 호출해야 하므로 인자만 비운다
  const ref = useCompanyLogoRef(stockCode ? null : companyId);
  const url = logoUrl(stockCode ?? ref?.stockCode, market ?? ref?.market);
  // 목록이 재사용되며 다른 기업으로 바뀔 수 있으므로 '깨짐'은 그 URL 에만 붙인다
  const [brokenUrl, setBrokenUrl] = useState<string | null>(null);
  const hasLogo = Boolean(url) && url !== brokenUrl;
  const color = industryColor(industry);
  return (
    <span
      className="avatar"
      style={{ width: size, height: size, borderRadius: radius ?? Math.round(size * 0.28), fontSize: Math.max(11, Math.round(size * 0.3)), background: hasLogo ? "#fff" : `linear-gradient(140deg, ${color}66, ${color}22)`, color: "#fff", border: `1px solid ${color}55` }}
      aria-hidden="true"
    >
      {hasLogo ? <img src={url!} alt="" loading="lazy" decoding="async" onError={() => setBrokenUrl(url)} /> : initials(name)}
    </span>
  );
}

/* ------------------------------ 배지 ------------------------------ */
/** sentiment 가 아직 분석 전(null)이어도 '분석 전' 대신 중립으로 보여준다 (제품 결정) */
export function SentimentBadge({ value, compact = false }: { value: Sentiment | null | undefined; compact?: boolean }) {
  const m = (value && SENTIMENT_META[value]) ?? SENTIMENT_META.NEUTRAL;
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
/** 대표 산업이 없는 기업(primaryIndustry: null)은 '미분류' 로 표시한다 */
export function IndustryBadge({ name }: { name: string | null | undefined }) {
  const label = name || UNCLASSIFIED_INDUSTRY;
  const c = industryColor(label);
  return (
    <span className="badge" style={{ background: `${c}22`, color: c }}>
      {label}
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
/** 시장 코드가 비어 있으면(백엔드가 "" 로 내려주는 기업) 빈 칸 대신 아무것도 그리지 않는다 */
export function MarketTag({ market }: { market: Market | null | undefined }) {
  if (!market) return null;
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
 * 빔 색은 관계 유형이 정하므로(협력 파랑·경쟁 빨강·공급/투자 보라 계열, lib/meta) 실제 간선을 가리킬 때는 color 로 그 색을 넘긴다.
 * 특정 간선을 가리키지 않는 자리만 기본값(중립 보라)을 쓴다.
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
          <button key={t} type="button" className={`chip chip-sm ${on ? "on" : ""}`} onClick={() => onToggle?.(t)} title={`${m.description} · ${m.directed ? "방향 있음 (펄스가 한쪽으로 흐름)" : "방향 없음 (점선)"} · 씬의 관계선도 이 색입니다`} style={{ opacity: on ? 1 : 0.55 }}>
            <i className="dot" style={{ background: m.color }} />
            <EdgeGlyph directed={m.directed} color={m.color} />
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
/**
 * 목록·상세 쿼리가 네트워크·서버 오류로 실패했을 때의 공통 안내.
 * <Empty>(필터 조건에 맞는 결과가 없음)와 시각적으로 구분해, 오류를 "결과 없음"으로 오인하지 않게 한다.
 */
export function ErrorNotice({ message = "데이터를 불러오지 못했습니다.", onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="empty error-notice">
      <div>{message}</div>
      {onRetry && (
        <button type="button" className="btn btn-g btn-sm mt-8" onClick={onRetry}>
          <Icon.Refresh /> 다시 시도
        </button>
      )}
    </div>
  );
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

/* ------------------------------ 다이얼로그 포커스 순환 ------------------------------ */
/**
 * 다이얼로그(모바일 서랍·바텀시트) 안에서 Tab/Shift+Tab 을 첫·마지막 초점 요소 사이에서 순환시킨다 — 스크림 뒤의 HUD 로 포커스가 새면
 * 래퍼의 ESC 핸들러가 닿지 않아 닫을 길이 없어진다. 컨테이너의 keydown 핸들러에서 부른다
 */
export function cycleTabFocus(e: ReactKeyboardEvent<HTMLElement>, container: HTMLElement | null) {
  if (e.key !== "Tab" || !container) return;
  const items = container.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])');
  if (items.length === 0) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
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
  /* 모바일 헤더의 ☰ — 탭·계정 서랍을 연다 */
  Menu: () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <path d="M4 7h16M4 12h16M4 17h16" />
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

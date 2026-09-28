import { useMemo } from 'react';
import { useGalaxy } from '../store/useGalaxy';
import {
  COMPANY_BY_ID,
  REL_TYPES,
  WINDOW,
  SECTORS,
  changePct,
  dateLabel,
  inferContext,
  neighborsOf,
  newsFor,
  normalizedPrice,
  priceAt,
  relationTimeline,
} from '../data/universe';

const pct = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
const won = (v) => `${Math.round(v).toLocaleString('ko-KR')}원`;
const corrColor = (c) => (c >= 0 ? '#ff8f8f' : '#6fb8ff');

const keyOf = (a, b) => (a < b ? `${a}|${b}` : `${b}|${a}`);

/**
 * 사전 정의된 설명이 없는 연결 — 통계와 사건으로 정황을 대신 제시한다.
 * (사람이 미리 알지 못한 관계를 "발견"하게 하는 것이 이 서비스의 차별점이므로,
 *  설명이 없다고 비워두지 않고 판단 재료를 준다)
 */
function InferredReason({ aId, bId, day }) {
  const ctx = useMemo(() => inferContext(aId, bId, day), [aId, bId, day]);
  const total = ctx.coMove + ctx.counterMove;
  return (
    <div className="reason none">
      사전 정의된 관계 없음 — 데이터에서만 관측된 연결.
      <div className="inferred">
        <div>
          <b>동반 급변</b> 최근 {WINDOW}거래일 중 <b className="num">{ctx.coMove}일</b> 같은 방향
          {total > 0 && <span className="num"> (반대 {ctx.counterMove}일)</span>}
        </div>
        {ctx.macro.length > 0 && (
          <div>
            <b>공통 사건</b> [{ctx.macro[ctx.macro.length - 1].tag}]{' '}
            {ctx.macro[ctx.macro.length - 1].title}
          </div>
        )}
        {ctx.news[0] && (
          <div>
            <b>기간 내 최대 뉴스</b> {ctx.news[0].headline}
          </div>
        )}
      </div>
    </div>
  );
}

/** 관계 1건 — 클릭하면 "왜 연결되었나 + 근거가 언제 바뀌었나"가 펼쳐진다 */
function Relation({ aId, bId, corr, type, reason, serendipity, day }) {
  const selectedEdge = useGalaxy((s) => s.selectedEdge);
  const selectEdge = useGalaxy((s) => s.selectEdge);
  const setHovered = useGalaxy((s) => s.setHovered);
  const focus = useGalaxy((s) => s.focus);

  const key = keyOf(aId, bId);
  const open = selectedEdge === key;
  const a = COMPANY_BY_ID[aId];
  const b = COMPANY_BY_ID[bId];
  const other = focus === aId ? b : focus === bId ? a : null;

  const history = useMemo(
    () => (open ? relationTimeline(aId, bId, { upTo: day }) : []),
    [open, aId, bId, day]
  );

  return (
    <div
      className={`rel ${open ? 'active' : ''}`}
      onClick={() => selectEdge(key)}
      onMouseEnter={() => other && setHovered(other.id)}
      onMouseLeave={() => setHovered(null)}
    >
      <div className="rel-top">
        <i
          className="dot"
          style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: serendipity ? 'var(--pink)' : REL_TYPES[type].color,
            flex: 'none',
          }}
        />
        <span className="nm">
          {other ? other.name : `${a.name} ↔ ${b.name}`}
        </span>
        {serendipity && <span className="badge seren">예상 밖</span>}
        <span className="corr num" style={{ color: corrColor(corr) }}>
          {corr >= 0 ? '+' : ''}
          {corr.toFixed(2)}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 5 }}>
        <span className="badge" style={{ color: REL_TYPES[type].color }}>
          {REL_TYPES[type].name}
        </span>
        {!other && (
          <span style={{ fontSize: 10.5, color: 'var(--dim)' }}>
            {SECTORS[a.sector].name} · {SECTORS[b.sector].name}
          </span>
        )}
      </div>

      {reason ? (
        <div className="reason">{reason}</div>
      ) : (
        <InferredReason aId={aId} bId={bId} day={day} />
      )}

      {open && (
        <div className="evidence">
          <div className="eyebrow" style={{ marginBottom: 5 }}>
            근거 변경 이력 (상관계수 급변 시점)
          </div>
          {history.length === 0 && (
            <div style={{ fontSize: 11, color: 'var(--dim)' }}>
              이 시점까지 유의미한 관계 변화가 감지되지 않았다.
            </div>
          )}
          {history.map((h) => (
            <div className="ev" key={h.day}>
              <span className="when num">{dateLabel(h.day)}</span>
              <span
                className="arrow num"
                style={{ color: h.delta >= 0 ? '#ff8f8f' : '#6fb8ff' }}
              >
                {h.from.toFixed(2)} → {h.to.toFixed(2)}
              </span>
              <span className="why">
                {h.cause
                  ? `${h.cause.kind === 'macro' ? '[거시] ' : ''}${h.cause.label}`
                  : '특정 뉴스 없이 동조성만 변화 — 수급/업황 요인 추정'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 은하 뷰 : 관계 랭킹                                                  */
/* ------------------------------------------------------------------ */
function GalaxyDetail({ edges, day }) {
  const hovered = useGalaxy((s) => s.hovered);
  const c = hovered ? COMPANY_BY_ID[hovered] : null;

  const stats = useMemo(() => {
    const seren = edges.filter((e) => e.serendipity).length;
    const avg = edges.length
      ? edges.reduce((s, e) => s + Math.abs(e.corr), 0) / edges.length
      : 0;
    const cross = edges.filter((e) => e.crossSector).length;
    return { seren, avg, cross };
  }, [edges]);

  const list = useMemo(
    () => (hovered ? edges.filter((e) => e.a === hovered || e.b === hovered) : edges.slice(0, 28)),
    [edges, hovered]
  );

  return (
    <>
      <div className="detail-head">
        <span className="eyebrow">{c ? '선택된 항성' : '은하 전체'}</span>
        <h2 style={c ? { color: SECTORS[c.sector].color } : undefined}>
          {c ? c.name : '관계 랭킹'}
        </h2>
        <div className="meta">
          {c
            ? `${c.ticker} · ${SECTORS[c.sector].name} · 시총 ${c.cap}조 · 클릭하면 이 기업 차원으로 워프`
            : `${dateLabel(day)} 기준 · 상관계수 상위 연결`}
        </div>
      </div>

      <div className="kpis">
        <div className="kpi">
          <span className="eyebrow">연결</span>
          <b className="num">{edges.length}</b>
        </div>
        <div className="kpi">
          <span className="eyebrow">섹터 교차</span>
          <b className="num">{stats.cross}</b>
        </div>
        <div className="kpi">
          <span className="eyebrow">예상 밖</span>
          <b className="num" style={{ color: 'var(--pink)' }}>
            {stats.seren}
          </b>
        </div>
      </div>

      <div className="scroll">
        {list.length === 0 && (
          <div className="empty">
            기준을 만족하는 연결이 없다.
            <br />
            좌측에서 |상관계수| 기준을 낮춰보자.
          </div>
        )}
        {list.map((e) => (
          <Relation
            key={e.key}
            aId={e.a}
            bId={e.b}
            corr={e.corr}
            type={e.type}
            reason={e.reason}
            serendipity={e.serendipity}
            day={day}
          />
        ))}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* 기업 차원 : 연결 + 뉴스                                              */
/* ------------------------------------------------------------------ */
function SystemDetail({ day }) {
  const focus = useGalaxy((s) => s.focus);
  const c = COMPANY_BY_ID[focus];

  const neighbors = useMemo(
    () => neighborsOf(focus, day, { threshold: 0.28, limit: 9 }),
    [focus, day]
  );
  const news = useMemo(
    () => newsFor(focus, { to: day }).slice().reverse().slice(0, 12),
    [focus, day]
  );
  const totalReturn = useMemo(() => normalizedPrice(focus, 0, day)[day], [focus, day]);

  return (
    <>
      <div className="detail-head">
        <span className="eyebrow">기업 차원</span>
        <h2 style={{ color: SECTORS[c.sector].color }}>{c.name}</h2>
        <div className="meta">
          {c.ticker} · {SECTORS[c.sector].name} · 시가총액 {c.cap}조
        </div>
      </div>

      <div className="kpis">
        <div className="kpi">
          <span className="eyebrow">종가</span>
          <b className="num">{won(priceAt(focus, day))}</b>
        </div>
        <div className="kpi">
          <span className="eyebrow">20일</span>
          <b className={`num ${changePct(focus, day, 20) >= 0 ? 'up' : 'down'}`}>
            {pct(changePct(focus, day, 20))}
          </b>
        </div>
        <div className="kpi">
          <span className="eyebrow">기간 누적</span>
          <b className={`num ${totalReturn >= 0 ? 'up' : 'down'}`}>{pct(totalReturn)}</b>
        </div>
      </div>

      <div className="scroll">
        <div className="sec" style={{ borderBottom: '1px solid var(--line)' }}>
          <div className="sec-head" style={{ margin: 0 }}>
            <span className="eyebrow">연결된 기업 {neighbors.length}</span>
            <span style={{ fontSize: 10, color: 'var(--dim)' }}>클릭 → 근거 펼치기</span>
          </div>
        </div>

        {neighbors.length === 0 && (
          <div className="empty">이 시점에는 기준을 넘는 연결이 없다.</div>
        )}
        {neighbors.map((n) => (
          <Relation
            key={n.id}
            aId={focus}
            bId={n.id}
            corr={n.corr}
            type={n.type}
            reason={n.reason}
            serendipity={n.crossSector && n.type === 'unknown' && Math.abs(n.corr) >= 0.45}
            day={day}
          />
        ))}

        <div className="sec" style={{ borderTop: '1px solid var(--line)' }}>
          <div className="sec-head" style={{ margin: 0 }}>
            <span className="eyebrow">뉴스 · 발생 후 5영업일 수익률</span>
          </div>
        </div>

        {news.length === 0 && <div className="empty">이 시점까지 수집된 뉴스가 없다.</div>}
        {news.map((n) => (
          <div className="newsitem" key={n.id}>
            <i
              className="bar"
              style={{ background: n.tone > 0 ? 'var(--good)' : 'var(--bad)' }}
            />
            <div style={{ flex: 1 }}>
              <div className="when num">
                {dateLabel(n.day)} · {n.tag} · {n.tone > 0 ? '호재' : '악재'}
              </div>
              <div>{n.headline}</div>
            </div>
            <div className={`imp num ${n.impact >= 0 ? 'up' : 'down'}`}>{pct(n.impact)}</div>
          </div>
        ))}
      </div>
    </>
  );
}

export default function DetailPanel({ edges }) {
  const focus = useGalaxy((s) => s.focus);
  const day = useGalaxy((s) => s.day);
  return (
    <div className="panel right">
      {focus ? <SystemDetail day={day} /> : <GalaxyDetail edges={edges} day={day} />}
    </div>
  );
}

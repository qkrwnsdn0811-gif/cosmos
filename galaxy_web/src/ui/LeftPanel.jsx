import { useMemo } from 'react';
import { useGalaxy } from '../store/useGalaxy';
import { COMPANIES, REL_TYPES, SECTORS, changePct } from '../data/universe';

const pct = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;

export default function LeftPanel() {
  const day = useGalaxy((s) => s.day);
  const threshold = useGalaxy((s) => s.threshold);
  const setThreshold = useGalaxy((s) => s.setThreshold);
  const activeTypes = useGalaxy((s) => s.activeTypes);
  const toggleType = useGalaxy((s) => s.toggleType);
  const onlySerendipity = useGalaxy((s) => s.onlySerendipity);
  const toggleSerendipity = useGalaxy((s) => s.toggleSerendipity);
  const hovered = useGalaxy((s) => s.hovered);
  const focus = useGalaxy((s) => s.focus);
  const setHovered = useGalaxy((s) => s.setHovered);
  const warpTo = useGalaxy((s) => s.warpTo);

  const rows = useMemo(
    () =>
      COMPANIES.map((c) => ({ ...c, chg: changePct(c.id, day, 20) })).sort((a, b) => b.cap - a.cap),
    [day]
  );

  const fill = `${((threshold - 0.2) / 0.6) * 100}%`;

  return (
    <div className="panel left">
      <div className="sec">
        <div className="sec-head">
          <span className="eyebrow">연결 기준 |상관계수|</span>
          <b className="num" style={{ fontSize: 13 }}>
            ≥ {threshold.toFixed(2)}
          </b>
        </div>
        <input
          className="slider"
          style={{ '--fill': fill }}
          type="range"
          min={0.2}
          max={0.8}
          step={0.01}
          value={threshold}
          onChange={(e) => setThreshold(parseFloat(e.target.value))}
        />
      </div>

      <div className="sec">
        <div className="sec-head">
          <span className="eyebrow">관계 유형</span>
        </div>
        <div className="chips">
          {Object.values(REL_TYPES).map((t) => (
            <button
              key={t.id}
              className={`chip ${activeTypes.has(t.id) ? 'on' : ''}`}
              onClick={() => toggleType(t.id)}
            >
              <i className="dot" style={{ background: t.color }} />
              {t.name}
            </button>
          ))}
        </div>
        <button
          className={`chip ${onlySerendipity ? 'on' : ''}`}
          style={{ marginTop: 7, width: '100%', justifyContent: 'center' }}
          onClick={toggleSerendipity}
        >
          <i className="dot" style={{ background: '#ff9de0' }} />
          예상 밖 연결만 보기
        </button>
      </div>

      <div className="sec" style={{ paddingBottom: 6 }}>
        <div className="sec-head">
          <span className="eyebrow">종목 · 20일 변동</span>
          <span style={{ fontSize: 10, color: 'var(--dim)' }}>클릭 → 워프</span>
        </div>
      </div>

      <div className="list">
        {rows.map((c) => (
          <button
            key={c.id}
            className={`row ${hovered === c.id || focus === c.id ? 'active' : ''}`}
            onMouseEnter={() => setHovered(c.id)}
            onMouseLeave={() => setHovered(null)}
            onClick={() => warpTo(c.id)}
          >
            <i className="dot" style={{ background: SECTORS[c.sector].color, color: SECTORS[c.sector].color }} />
            <span className="nm">{c.name}</span>
            <span className={`pc num ${c.chg >= 0 ? 'up' : 'down'}`}>{pct(c.chg)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

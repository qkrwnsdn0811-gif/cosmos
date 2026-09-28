import { useMemo } from 'react';
import { useGalaxy } from '../store/useGalaxy';
import { ALL_NEWS, MACRO_EVENTS, N_DAYS, dateLabel } from '../data/universe';

export default function Timeline() {
  const day = useGalaxy((s) => s.day);
  const setDay = useGalaxy((s) => s.setDay);
  const stepDay = useGalaxy((s) => s.stepDay);
  const playing = useGalaxy((s) => s.playing);
  const togglePlay = useGalaxy((s) => s.togglePlay);

  const pos = (d) => `${(d / (N_DAYS - 1)) * 100}%`;

  // 현재 시점에 영향을 주고 있는 거시 이벤트 (최근 20영업일 이내)
  const macroNow = useMemo(() => {
    const hit = MACRO_EVENTS.filter((m) => m[0] <= day && day - m[0] <= 20).pop();
    return hit ? { day: hit[0], title: hit[1], tone: hit[2], tag: hit[4] } : null;
  }, [day]);

  return (
    <div className="panel timeline">
      <div className="tl-head">
        <button className="btn" onClick={togglePlay} style={{ width: 78 }}>
          {playing ? '❚❚ 정지' : '▶ 재생'}
        </button>
        <button className="btn ghost" onClick={() => stepDay(-20)}>
          −20일
        </button>
        <button className="btn ghost" onClick={() => stepDay(20)}>
          +20일
        </button>
        <span className="eyebrow" style={{ marginLeft: 4 }}>
          관측 시점
        </span>
        <span className="tl-date num">{dateLabel(day)}</span>

        {macroNow && (
          <div className="macro-now">
            <b style={{ fontSize: 10, letterSpacing: '0.08em' }}>[{macroNow.tag}]</b>
            <span>{macroNow.title}</span>
          </div>
        )}
      </div>

      <div className="tl-track">
        <div className="tl-marks">
          {ALL_NEWS.map((n) => (
            <i
              key={n.id}
              className="tl-mark news"
              style={{
                left: pos(n.day),
                background: n.tone > 0 ? 'var(--good)' : 'var(--bad)',
              }}
              title={`${dateLabel(n.day)} ${n.headline}`}
            />
          ))}
          {MACRO_EVENTS.map((m) => (
            <i
              key={m[0]}
              className="tl-mark macro"
              style={{ left: pos(m[0]) }}
              title={`${dateLabel(m[0])} ${m[1]}`}
            />
          ))}
        </div>
        <input
          className="slider"
          style={{ '--fill': `${(day / (N_DAYS - 1)) * 100}%` }}
          type="range"
          min={0}
          max={N_DAYS - 1}
          value={day}
          onChange={(e) => setDay(parseInt(e.target.value, 10))}
        />
      </div>

      <div className="tl-legend">
        <i>▌긴 눈금 = 거시 이벤트</i>
        <i>
          <b style={{ color: 'var(--good)' }}>▌</b> 호재 뉴스
        </i>
        <i>
          <b style={{ color: 'var(--bad)' }}>▌</b> 악재 뉴스
        </i>
        <i style={{ marginLeft: 'auto' }}>
          시점을 옮기면 상관계수가 다시 계산되어 관계도가 재구성된다
        </i>
      </div>
    </div>
  );
}

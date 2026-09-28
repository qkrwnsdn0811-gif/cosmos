import { useGalaxy } from '../store/useGalaxy';
import { COMPANY_BY_ID, SECTORS, WINDOW, dateLabel } from '../data/universe';

export default function Hud({ edgeCount }) {
  const phase = useGalaxy((s) => s.phase);
  const focus = useGalaxy((s) => s.focus);
  const day = useGalaxy((s) => s.day);
  const showLabels = useGalaxy((s) => s.showLabels);
  const toggleLabels = useGalaxy((s) => s.toggleLabels);
  const backToGalaxy = useGalaxy((s) => s.backToGalaxy);

  const company = focus ? COMPANY_BY_ID[focus] : null;

  return (
    <div className="panel hud">
      <div className="brand">
        <h1>GALAXY</h1>
        <small>뉴스 × 주가 상관관계 탐사</small>
      </div>

      <div className="hud-sep" />

      <div className="hud-stat">
        <span className="eyebrow">현재 좌표</span>
        <b style={company ? { color: SECTORS[company.sector].color } : undefined}>
          {company ? company.name : '은하 전체'}
        </b>
      </div>

      <div className="hud-stat">
        <span className="eyebrow">관측 시점</span>
        <b className="num">{dateLabel(day)}</b>
      </div>

      <div className="hud-stat">
        <span className="eyebrow">연결 수</span>
        <b className="num">{edgeCount}</b>
      </div>

      <div className="hud-stat" style={{ minWidth: 108 }}>
        <span className="eyebrow">상관 윈도우</span>
        <b className="num">{WINDOW}일 롤링</b>
      </div>

      <div className="hud-spacer" />

      <div className="hud-actions">
        <button className={`btn ghost ${showLabels ? 'on' : ''}`} onClick={toggleLabels}>
          라벨
        </button>
        {focus && phase !== 'warp' && (
          <button className="btn" onClick={backToGalaxy}>
            ← 은하로 복귀
          </button>
        )}
      </div>
    </div>
  );
}

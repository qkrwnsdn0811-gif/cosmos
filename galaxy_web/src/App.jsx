import { useEffect } from 'react';
import Scene from './three/Scene';
import { useGraph } from './three/useGraph';
import Hud from './ui/Hud';
import LeftPanel from './ui/LeftPanel';
import DetailPanel from './ui/DetailPanel';
import Timeline from './ui/Timeline';
import Intro from './ui/Intro';
import { useGalaxy } from './store/useGalaxy';
import { COMPANY_BY_ID } from './data/universe';

export default function App() {
  const phase = useGalaxy((s) => s.phase);
  const focus = useGalaxy((s) => s.focus);
  const warpTarget = useGalaxy((s) => s.warpTarget);
  const edges = useGraph();

  // 키보드 단축키
  useEffect(() => {
    const onKey = (e) => {
      const s = useGalaxy.getState();
      if (s.phase === 'intro') {
        if (e.key === 'Enter' || e.key === ' ') s.begin();
        return;
      }
      if (s.phase === 'warp') return;
      if (e.key === 'Escape' && s.focus) s.backToGalaxy();
      else if (e.key === ' ') {
        e.preventDefault();
        s.togglePlay();
      } else if (e.key === 'ArrowRight') s.stepDay(e.shiftKey ? 20 : 5);
      else if (e.key === 'ArrowLeft') s.stepDay(e.shiftKey ? -20 : -5);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const warping = phase === 'warp';
  const dest = warpTarget ? COMPANY_BY_ID[warpTarget]?.name : '은하 전체';

  return (
    <>
      <div className="stage">
        <Scene />
      </div>

      {phase === 'intro' ? (
        <Intro />
      ) : (
        <div className={`overlay ${warping ? 'warping' : ''}`}>
          <Hud edgeCount={edges.length} />
          <LeftPanel />
          <DetailPanel edges={edges} />
          <Timeline />

          {warping && (
            <div className="warp-caption">
              <div>
                <div className="t1">Dimension Shift</div>
                <div className="t2">{dest}</div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 워프가 끝난 직후 짧은 안내 */}
      {!warping && focus === null && phase === 'galaxy' && (
        <div
          style={{
            position: 'fixed',
            bottom: 118,
            left: '50%',
            transform: 'translateX(-50%)',
            fontSize: 11,
            letterSpacing: '0.16em',
            color: 'var(--dim)',
            pointerEvents: 'none',
          }}
        >
          항성을 클릭하면 해당 기업의 차원으로 워프합니다 · 드래그 회전 · 휠 확대
        </div>
      )}
    </>
  );
}

import { useGalaxy } from '../store/useGalaxy';
import { COMPANIES, N_DAYS, dateLabel } from '../data/universe';

export default function Intro() {
  const begin = useGalaxy((s) => s.begin);
  return (
    <div className="intro">
      <div className="intro-box">
        <div className="kicker">News × Price Correlation Explorer</div>
        <h1>GALAXY</h1>
        <h2>기업 관계를 차원 이동하듯 탐색한다</h2>
        <p>
          뉴스와 과거 주가에서 계산한 <b>롤링 상관계수</b>로 기업 사이의 중력을 그린다. 항성을
          선택하면 그 기업의 차원으로 워프해 관계가 형성된 이유와, 그 근거가 언제·왜 바뀌었는지를
          확인할 수 있다.
        </p>
        <button className="intro-cta" onClick={begin}>
          탐사 시작
        </button>
        <div className="intro-note">
          MVP 범위 · 국내 {COMPANIES.length}개 종목 · {dateLabel(0)} ~ {dateLabel(N_DAYS - 1)} ·
          영업일 {N_DAYS}일
        </div>
      </div>
    </div>
  );
}

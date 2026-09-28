import { useState } from "react";
import ArtScene, { type LabOptions } from "./ArtScene";
import { LAB } from "./toon";
import "./lab.css";

const SWATCHES: { label: string; color: string }[] = [
  { label: "배경", color: LAB.bg },
  { label: "배경 위", color: LAB.bgTop },
  { label: "항성", color: LAB.star },
  { label: "항성 핫", color: LAB.starHot },
  { label: "관계 빔", color: LAB.beam },
  { label: "강조", color: LAB.accent },
  { label: "틸 바다", color: LAB.planets.teal.ocean },
  { label: "틸 대륙", color: LAB.planets.teal.land },
  { label: "바이올렛 바다", color: LAB.planets.violet.ocean },
  { label: "바이올렛 대륙", color: LAB.planets.violet.land },
  { label: "블루 바다", color: LAB.planets.blue.ocean },
  { label: "블루 대륙", color: LAB.planets.blue.land },
];

/**
 * 아트 디렉션 랩 (/lab/art) — 항성 1, 행성 3종, 관계 빔, 로고 이름표만 놓고 형태·머티리얼·팔레트를 확정하는 실험 페이지.
 * 내비게이션에는 노출하지 않는다.
 */
export default function ArtLabPage() {
  const [options, setOptions] = useState<LabOptions>({ steps: 3, glow: true, spin: true });
  const set = (patch: Partial<LabOptions>) => setOptions((o) => ({ ...o, ...patch }));

  return (
    <div className="art-lab">
      <div className="art-lab-canvas">
        <ArtScene options={options} />
      </div>
      <aside className="lab-panel card">
        <div className="lab-kicker">ART DIRECTION LAB</div>
        <h2>장난감 우주</h2>
        <p className="lab-desc">짙은 네이비 위에 채도 높은 플랫 컬러, 둥근 저폴리, 셀 셰이딩. 웅장함은 개수에서, 귀여움은 이 일관성에서 온다.</p>

        <section>
          <h3>셀 셰이딩 단계</h3>
          <div className="seg">
            {[2, 3, 4].map((n) => (
              <button key={n} type="button" className={options.steps === n ? "on" : ""} onClick={() => set({ steps: n })}>
                {n}단
              </button>
            ))}
          </div>
        </section>
        <section className="lab-toggles">
          <label>
            <input type="checkbox" checked={options.glow} onChange={(e) => set({ glow: e.target.checked })} />
            글로우 (코로나 · 빔 헤일로 · 약한 블룸)
          </label>
          <label>
            <input type="checkbox" checked={options.spin} onChange={(e) => set({ spin: e.target.checked })} />
            자전 · 자동 회전
          </label>
        </section>

        <section>
          <h3>화면의 요소</h3>
          <ul className="lab-legend">
            <li>
              <b>항성</b> 산업 하나. 저폴리 구체 + 따뜻한 코로나. 성단의 중심이 된다
            </li>
            <li>
              <b>행성 3종</b> 기업. 대륙형 · 고리형 · 위성형을 시드로 섞어 같은 세트처럼 보이게 한다
            </li>
            <li>
              <b>로고 이름표</b> 행성 앞에 떠 있는 흰 원판. 멀리서는 사라지고 가까이 가면 나타나게 할 예정
            </li>
            <li>
              <b>관계 빔</b> 한 가지 주황. 점수는 굵기와 밝기, 방향은 흐르는 펄스로만 말한다
            </li>
          </ul>
        </section>

        <section>
          <h3>팔레트</h3>
          <div className="swatches">
            {SWATCHES.map((s) => (
              <div key={s.label} className="swatch" title={s.color}>
                <i style={{ background: s.color }} />
                <span>{s.label}</span>
                <code>{s.color}</code>
              </div>
            ))}
          </div>
        </section>
        <p className="lab-foot">드래그 회전 · 휠 확대. 여기서 확정한 규칙을 은하 전체에 적용한다.</p>
      </aside>
    </div>
  );
}

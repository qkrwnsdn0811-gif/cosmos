import type { ReactNode } from "react";
import { CAMERA_PRESETS, CAMERA_UI, type CameraPresetId } from "@/lib/guide";
import { useGalaxy } from "@/store/galaxy";
import { Icon } from "@/components/ui";

const ICONS: Record<CameraPresetId, ReactNode> = {
  orbit: <Icon.CamOrbit />,
  top: <Icon.CamTop />,
  front: <Icon.CamFront />,
};

/**
 * 카메라 프리셋 3종 — 궤도(둘러보기) · 위에서(거리 비교) · 정면에서(고도 비교). 기획서 §5-3.
 * 같은 프리셋을 다시 눌러도 카메라가 그 자세로 돌아간다(스토어가 횟수를 올린다) — 드래그로 흐트러진 시점을 되돌리는 버튼이기도 하다.
 * 은하 뷰·기업 중심 뷰 어디서나 같은 컴포넌트, 자세는 Scene 의 poseFor 가 뷰 종류에 맞게 만든다.
 */
export default function CameraPresets() {
  const preset = useGalaxy((s) => s.cameraPreset);
  const nonce = useGalaxy((s) => s.cameraPresetNonce);
  const setCameraPreset = useGalaxy((s) => s.setCameraPreset);
  const current = CAMERA_PRESETS.find((p) => p.id === preset) ?? CAMERA_PRESETS[0];
  return (
    <div className="cam-presets">
      {/* key=nonce: 누를 때마다 다시 마운트돼 켜진 버튼의 flash 애니메이션이 재생된다 — 같은 프리셋 재클릭(시점 되돌리기)도 눌렸다는 표시가 남는다 */}
      <div className="seg cam-seg" role="group" aria-label={CAMERA_UI.groupLabel} key={nonce}>
        {CAMERA_PRESETS.map((p) => (
          <button key={p.id} type="button" className={preset === p.id ? "on" : ""} aria-pressed={preset === p.id} aria-label={`${p.label} — ${p.hint}`} title={`${p.label} — ${p.hint} · ${CAMERA_UI.reapply}`} onClick={() => setCameraPreset(p.id)}>
            {ICONS[p.id]}
            <span>{p.label}</span>
          </button>
        ))}
      </div>
      <div className="cam-hint meta" aria-live="polite">
        {current.hint}
      </div>
    </div>
  );
}

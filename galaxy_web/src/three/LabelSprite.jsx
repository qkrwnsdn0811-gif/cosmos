import { useMemo } from 'react';
import * as THREE from 'three';

/**
 * 한글 라벨을 캔버스 텍스처 → 스프라이트로 렌더링한다.
 * (troika/Text 는 기본 폰트에 한글 글리프가 없어 두부가 나오므로 캔버스로 처리)
 */
const cache = new Map();

function makeLabel(text, color, weight, px) {
  const key = `${text}|${color}|${weight}|${px}`;
  const hit = cache.get(key);
  if (hit) return hit;

  const font = `${weight} ${px}px Pretendard, "Pretendard Variable", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif`;
  const measure = document.createElement('canvas').getContext('2d');
  measure.font = font;
  const w = Math.ceil(measure.measureText(text).width);
  const padX = px * 0.5;
  const padY = px * 0.42;

  const canvas = document.createElement('canvas');
  canvas.width = w + padX * 2;
  canvas.height = px + padY * 2;
  const ctx = canvas.getContext('2d');
  ctx.font = font;
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';

  // 어두운 배경 위에서도 읽히도록 소프트 글로우 + 본문 2패스
  ctx.shadowColor = 'rgba(0,0,0,0.95)';
  ctx.shadowBlur = px * 0.55;
  ctx.fillStyle = 'rgba(0,0,0,0.9)';
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);
  ctx.shadowBlur = 0;
  ctx.fillStyle = color;
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.anisotropy = 4;
  const entry = { tex, aspect: canvas.width / canvas.height };
  cache.set(key, entry);
  return entry;
}

export default function LabelSprite({
  text,
  color = '#e6eefc',
  size = 1.6,
  weight = 600,
  px = 48,
  opacity = 1,
  ...props
}) {
  const { tex, aspect } = useMemo(
    () => makeLabel(text, color, weight, px),
    [text, color, weight, px]
  );
  return (
    <sprite scale={[size * aspect, size, 1]} {...props}>
      <spriteMaterial
        map={tex}
        transparent
        opacity={opacity}
        depthWrite={false}
        toneMapped={false}
      />
    </sprite>
  );
}

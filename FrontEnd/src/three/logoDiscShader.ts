/**
 * 로고 행성 셰이더 — 기업 행성 = 카메라 정면 원판 하나에 원형으로 자른 로고(InstancedMesh 1회, CompanyNodes·GhostNodes).
 * 인스턴스 행렬은 매 프레임 카메라 방향으로 정렬(빌보드)되므로 화면 어디에 있어도 원으로 보인다.
 * 가장자리에는 얇은 산업색 테두리를 둘러 성단(산업)이 로고 사이에서도 읽히게 한다.
 * 인스턴스 속성: aColor(산업색) · aBright(밝기 — 호버·중심·강조) · aEmph(강조, 낮을수록 채도를 뺀다) · aAlpha(팝인·숨김) · aCell(아틀라스 셀 원점)
 */
export const discVertex = /* glsl */ `
  attribute vec3 aColor;
  attribute float aBright;
  attribute float aEmph;
  attribute float aAlpha;
  attribute vec2 aCell;
  varying vec3 vColor;
  varying vec2 vUv;
  varying float vBright;
  varying float vEmph;
  varying float vAlpha;
  varying vec2 vCell;
  void main() {
    vColor = aColor; vUv = uv; vBright = aBright; vEmph = aEmph; vAlpha = aAlpha; vCell = aCell;
    gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
  }
`;

/**
 * 밝기 상한 — 흰 바탕 로고가 블룸 임계(0.88)를 넘어 행성마다 번지지 않게 평소에는 그 아래로 둔다.
 * 호버(aBright 1.15)에서만 임계를 살짝 넘어 은은하게 빛난다
 */
const GAIN = 0.86;

export const discFragment = /* glsl */ `
  uniform sampler2D uAtlas;
  uniform float uCell;      // 아틀라스 셀 크기 (uv 단위)
  varying vec3 vColor;
  varying vec2 vUv;
  varying float vBright;
  varying float vEmph;
  varying float vAlpha;
  varying vec2 vCell;
  void main() {
    if (vAlpha < 0.01) discard;
    vec2 p = vUv * 2.0 - 1.0;
    float r = length(p);
    float aa = fwidth(r) * 1.2;
    float mask = 1.0 - smoothstep(1.0 - aa, 1.0, r);
    if (mask < 0.004) discard;
    // 아틀라스는 캔버스 좌표(y 아래)라 v 를 뒤집어 읽는다
    vec2 uv = vec2(vUv.x, 1.0 - vUv.y);
    vec3 col = texture2D(uAtlas, vCell + uv * uCell).rgb;
    // 산업색 테두리: 반지름의 8%, 작게 그려져도 1.5px 남짓은 남긴다
    float rimW = max(0.08, fwidth(r) * 1.5);
    float rim = smoothstep(1.0 - rimW - aa, 1.0 - rimW, r);
    col = mix(col, vColor, rim);
    // 흐림(호버 바깥·판단 보류): 어둡게 하는 것과 함께 채도를 빼서 로고 색이 강조된 행성과 다투지 않게 한다
    float grey = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(vec3(grey), col, mix(0.3, 1.0, vEmph));
    col *= vBright * ${GAIN.toFixed(2)};
    gl_FragColor = vec4(col, mask * vAlpha);
  }
`;

/** 원판 피킹 — uv(레이캐스트 평면 좌표)가 원 안인지. 셰이더의 mask 와 같은 원이다 */
export function discHit(uv: { x: number; y: number }): boolean {
  return Math.hypot(uv.x - 0.5, uv.y - 0.5) <= 0.5;
}

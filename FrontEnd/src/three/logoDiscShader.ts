/**
 * 로고 이름표 셰이더 — 행성 곁에 떠 있는 카메라 정면 원판. 흰 바탕 위 로고, 얇은 산업색 테두리.
 * 인스턴스 행렬은 매 프레임 카메라 방향으로 정렬(빌보드)되므로 화면 어디에 있어도 원으로 보인다.
 * 인스턴스 속성: aColor(산업색) · aBright(호버/중심) · aEmph(강조) · aAlpha(거리 페이드) · aCell(아틀라스 셀 원점)
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
    // 산업색 테두리: 반지름의 9%, 최소 1px 정도 폭
    float rimW = max(0.09, fwidth(r) * 1.6);
    float rim = smoothstep(1.0 - rimW - aa, 1.0 - rimW, r);
    col = mix(col, vColor, rim);
    col *= vBright;
    col *= mix(0.35, 1.0, vEmph);
    gl_FragColor = vec4(col, mask * vAlpha);
  }
`;

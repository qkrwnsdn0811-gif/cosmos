/**
 * 이름표 셰이더 — 로고 행성 바로 위에 뜨는 카메라 정면 캡슐 (InstancedMesh 1회).
 * 행성이 곧 로고 원판이라 이름표에는 로고가 없다. 평소에는 아무것도 띄우지 않고, 호버·선택한 기업(과 기업 중심 뷰의 중심)에서만
 * 기업명을, 호버 기업의 이웃에는 역할 태그(공급사·고객…)를 띄운다 — 어느 쪽도 없으면 폭(aspect)이 0 이라 그리지 않는다.
 *
 * 로컬 좌표 vQ 는 "칩 높이 = 1" 단위다. x ∈ [0, aspect], y ∈ [0, 1].
 *  - 어두운 네이비 캡슐 + 얇은 산업색 테두리(행성 테두리와 같은 색이라 어느 행성의 이름인지 이어진다)
 *  - 안쪽 왼쪽부터 역할 태그(선택) · 기업명(선택)
 * 지오메트리는 PlaneGeometry(1, 1) 을 y 로 +0.5 옮긴 것이고, 인스턴스 행렬의 x 스케일을 aspect 배로 늘려
 * 레이캐스트 평면과 그려지는 사각형이 일치하게 한다. 피킹은 CompanyNodes 가 raycast 를 감싸 chipHit(같은 캡슐 판정)을
 * 통과한 히트만 남기므로, 투명한 모서리는 R3F 이벤트 시스템에 아예 보이지 않는다.
 *
 * 인스턴스 속성: aColor(산업색) · aName(이름 셀 u,v,w,h; w=0 없음) · aAspect(칩 폭/높이)
 *              · aState = (alpha, emph, bright, nameOn) · aRole(역할 태그 셀, w=0 없음) · aRoleColor
 * 프레임마다 바뀌는 스칼라 넷은 vec4 하나로 묶었다 — 정점 속성 수를 WebGL2 최소 보장치 16 개 아래로 넉넉히 유지한다.
 */
/** 캡슐 바탕색 — 블룸 임계(0.88) 아래로 어둡게 유지한다 */
export const CHIP_PILL = "#141a3a";
/**
 * 기업명 글자 영역의 높이 — 칩 높이(1) 대비. 이름 아틀라스 행(ROW 44, em 36)을 이 높이에 세로로 꽉 채우므로
 * 화면 글자 크기는 CHIP_PX(CompanyNodes) × 이 값 × 36/44 다 — 32px × 0.8 × 0.82 ≈ 21px.
 * 로고 원이 있던 시절의 칩(40px × 0.68 ≈ 22px)과 글자 크기는 거의 같고, 캡슐만 얇아졌다.
 */
export const CHIP_NAME_H = 0.8;
/** 캡슐 안쪽 좌우 여백 */
const PAD = 0.3;
/** 역할 태그와 기업명 사이 */
const GAP = 0.15;
/** 역할 태그 폭/높이 — 이름 아틀라스의 역할 라벨 셀(w, h)에서. 셰이더 (b) 의 roleW 와 같은 식이다 */
export function roleWidth(w: number, h: number): number {
  return h > 0 ? (w / h) * 0.8 : 0;
}
/**
 * 칩 폭/높이 — 역할 태그(roleW, 없으면 0)와 기업명(nameAspect = 이름 셀 폭/높이, nameOn 이 1 일 때만)을 담는 폭.
 * 둘 다 없으면 0 (칩을 띄우지 않는다). 셰이더가 같은 배치(PAD·GAP)로 그리므로 두 곳이 어긋나면 글자가 잘리거나 빈 폭이 남는다
 */
export function chipAspect(nameAspect: number, roleW: number, nameOn: number): number {
  const name = nameOn > 0 && nameAspect > 0 ? nameAspect * CHIP_NAME_H : 0;
  const inner = roleW + name + (roleW > 0 && name > 0 ? GAP : 0);
  return inner > 0 ? Math.max(1, inner + PAD * 2) : 0;
}

export const chipVertex = /* glsl */ `
  attribute vec3 aColor;
  attribute vec4 aName;
  attribute float aAspect;
  attribute vec4 aState;
  attribute vec4 aRole;
  attribute vec3 aRoleColor;
  varying vec2 vQ;
  varying float vAspect;
  varying vec3 vColor;
  varying vec4 vName;
  varying float vAlpha;
  varying float vEmph;
  varying float vBright;
  varying float vNameOn;
  varying vec4 vRole;
  varying vec3 vRoleColor;
  void main() {
    vQ = vec2(uv.x * aAspect, uv.y);
    vAspect = aAspect; vColor = aColor; vName = aName;
    vAlpha = aState.x; vEmph = aState.y; vBright = aState.z; vNameOn = aState.w;
    vRole = aRole; vRoleColor = aRoleColor;
    gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
  }
`;

export const chipFragment = /* glsl */ `
  uniform sampler2D uNames;   // 이름 아틀라스
  uniform vec3 uPill;
  varying vec2 vQ;
  varying float vAspect;
  varying vec3 vColor;
  varying vec4 vName;
  varying float vAlpha;
  varying float vEmph;
  varying float vBright;
  varying float vNameOn;
  varying vec4 vRole;
  varying vec3 vRoleColor;

  void main() {
    if (vAlpha < 0.01 || vAspect <= 0.0) discard;
    // 픽셀 하나의 크기 (칩 높이 단위) — 안티에일리어싱 폭
    float aa = max(fwidth(vQ.y), 1e-4) * 1.2;
    float dim = vBright * mix(0.4, 1.0, vEmph);

    // (a) 캡슐 마스크 + 산업색 테두리
    float d = length(vQ - vec2(clamp(vQ.x, 0.5, vAspect - 0.5), 0.5)) - 0.5;
    float mask = 1.0 - smoothstep(-aa, aa, d);
    if (mask < 0.004) discard;
    vec3 col = mix(uPill, vColor, smoothstep(-0.07 - aa, -0.07 + aa, d) * 0.9);

    // (b) 역할 태그 — 유형색 작은 캡슐 + 어두운 글자 (aRole.w > 0 일 때만)
    float roleW = vRole.w > 0.0 ? (vRole.z / vRole.w) * 0.8 : 0.0;
    if (roleW > 0.0) {
      float x0 = ${PAD.toFixed(2)};
      float rd = length(vQ - vec2(clamp(vQ.x, x0 + 0.38, x0 + roleW - 0.38), 0.5)) - 0.38;
      float tag = 1.0 - smoothstep(-aa, aa, rd);
      if (tag > 0.001) {
        float ru = clamp((vQ.x - x0) / roleW, 0.0, 1.0);
        float ry = (vQ.y - 0.1) / 0.8;
        vec4 g = texture2D(uNames, vec2(vRole.x + ru * vRole.z, vRole.y + (1.0 - clamp(ry, 0.0, 1.0)) * vRole.w));
        // 글자는 흰색으로 구워져 있으니 r*a 가 글자 알파 (그림자는 제외)
        float glyph = g.r * g.a * step(0.0, ry) * step(ry, 1.0);
        col = mix(col, mix(vRoleColor, vec3(0.06, 0.08, 0.16), glyph), tag);
      }
    }

    // (c) 기업명 — 언제나 눌리지 않은 제 비율(nameFull)로 그린다. 폭이 모자라면 눌러 넣는 대신 그만큼만 드러낸다
    float nameX = ${PAD.toFixed(2)} + roleW + (roleW > 0.0 ? ${GAP.toFixed(2)} : 0.0);
    float nameW = vAspect - ${PAD.toFixed(2)} - nameX;
    float nameFull = (vName.z / max(vName.w, 1e-6)) * ${CHIP_NAME_H.toFixed(2)};
    if (vName.w > 0.0 && vNameOn > 0.01 && nameW > 0.02 && nameFull > 0.0 && vQ.x >= nameX && vQ.x <= nameX + nameW) {
      float nu = (vQ.x - nameX) / nameFull;
      float ny = (vQ.y - ${((1 - CHIP_NAME_H) / 2).toFixed(2)}) / ${CHIP_NAME_H.toFixed(2)};
      if (nu <= 1.0 && ny >= 0.0 && ny <= 1.0) {
        vec4 tx = texture2D(uNames, vec2(vName.x + nu * vName.z, vName.y + (1.0 - ny) * vName.w));
        col = mix(col, tx.rgb, tx.a * vNameOn);
      }
    }

    col *= dim;
    gl_FragColor = vec4(col, mask * 0.94 * vAlpha);
  }
`;

/**
 * 칩 피킹 — uv(레이캐스트 평면 좌표)가 캡슐 안인지. 투명한 모서리는 제외한다.
 * 셰이더의 (a) 와 같은 식이다.
 */
export function chipHit(aspect: number, uv: { x: number; y: number }): boolean {
  if (!(aspect > 0)) return false;
  const qx = uv.x * aspect;
  const cx = Math.min(Math.max(qx, 0.5), aspect - 0.5);
  return Math.hypot(qx - cx, uv.y - 0.5) <= 0.5;
}

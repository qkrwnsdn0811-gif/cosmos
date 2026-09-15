/**
 * 이름표 칩 셰이더 — 행성 위에 깃발처럼 떠 있는 카메라 정면 캡슐 (InstancedMesh 1회).
 *
 * 로컬 좌표 vQ 는 "칩 높이 = 1" 단위다. x ∈ [0, aspect], y ∈ [-0.35, 1].
 *  - y < 0        : 산업색 가느다란 스템 (행성 꼭대기에서 올라온다)
 *  - y ∈ [0, 1]   : 어두운 네이비 캡슐. 왼쪽 원(중심 0.5, 반지름 0.42)에 로고 아틀라스 셀, 이어서 역할 태그(선택)·기업명
 * 지오메트리는 PlaneGeometry(1, 1.35) 를 y 로 +0.325 옮긴 것이고, 인스턴스 행렬의 x 스케일을 aspect 배로 늘려
 * 레이캐스트 평면과 그려지는 사각형이 일치하게 한다. 피킹은 CompanyNodes 가 raycast 를 감싸 chipHit(같은 캡슐 판정)을
 * 통과한 히트만 남기므로, 스템·투명 모서리는 R3F 이벤트 시스템에 아예 보이지 않는다.
 *
 * 인스턴스 속성: aColor(산업색) · aCell(로고 셀 원점) · aName(이름 셀 u,v,w,h; w=0 없음) · aAspect(칩 폭/높이)
 *              · aState = (alpha, emph, bright, nameOn) · aRole(역할 태그 셀, w=0 없음) · aRoleColor
 * 프레임마다 바뀌는 스칼라 넷은 vec4 하나로 묶었다 — 따로 두면 position·uv·instanceMatrix(4) 를 합쳐 정점 속성이
 * WebGL2 최소 보장치 16 개에 꽉 차서, 속성 하나만 더 붙어도(D 단계 태그 알파 등) 링크가 깨질 수 있다.
 */
export const CHIP_H = 1.35;
/** 스템이 차지하는 아래쪽 높이 */
export const CHIP_STEM = 0.35;
/** 캡슐 바탕색 — 블룸 임계(0.88) 아래로 어둡게 유지한다 */
export const CHIP_PILL = "#141a3a";

export const chipVertex = /* glsl */ `
  attribute vec3 aColor;
  attribute vec2 aCell;
  attribute vec4 aName;
  attribute float aAspect;
  attribute vec4 aState;
  attribute vec4 aRole;
  attribute vec3 aRoleColor;
  varying vec2 vQ;
  varying float vAspect;
  varying vec3 vColor;
  varying vec2 vCell;
  varying vec4 vName;
  varying float vAlpha;
  varying float vEmph;
  varying float vBright;
  varying float vNameOn;
  varying vec4 vRole;
  varying vec3 vRoleColor;
  void main() {
    vQ = vec2(uv.x * aAspect, uv.y * ${CHIP_H.toFixed(2)} - ${CHIP_STEM.toFixed(2)});
    vAspect = aAspect; vColor = aColor; vCell = aCell; vName = aName;
    vAlpha = aState.x; vEmph = aState.y; vBright = aState.z; vNameOn = aState.w;
    vRole = aRole; vRoleColor = aRoleColor;
    gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
  }
`;

export const chipFragment = /* glsl */ `
  uniform sampler2D uAtlas;   // 로고 아틀라스
  uniform float uCell;        // 로고 셀 크기 (uv 단위)
  uniform sampler2D uNames;   // 이름 아틀라스
  uniform vec3 uPill;
  varying vec2 vQ;
  varying float vAspect;
  varying vec3 vColor;
  varying vec2 vCell;
  varying vec4 vName;
  varying float vAlpha;
  varying float vEmph;
  varying float vBright;
  varying float vNameOn;
  varying vec4 vRole;
  varying vec3 vRoleColor;

  void main() {
    if (vAlpha < 0.01) discard;
    // 픽셀 하나의 크기 (칩 높이 단위) — 안티에일리어싱 폭
    float aa = max(fwidth(vQ.y), 1e-4) * 1.2;
    float dim = vBright * mix(0.4, 1.0, vEmph);

    // (a) 스템 — 아래로 갈수록 옅어져 행성에 묻힌다
    if (vQ.y < 0.0) {
      float stem = 1.0 - smoothstep(0.03 - aa, 0.03 + aa, abs(vQ.x - 0.5));
      stem *= smoothstep(-0.35, -0.22, vQ.y);
      if (stem < 0.004) discard;
      gl_FragColor = vec4(vColor * dim, stem * 0.7 * vAlpha);
      return;
    }

    // (b) 캡슐 마스크
    float d = length(vQ - vec2(clamp(vQ.x, 0.5, vAspect - 0.5), 0.5)) - 0.5;
    float mask = 1.0 - smoothstep(-aa, aa, d);
    if (mask < 0.004) discard;
    vec3 col = uPill;
    float alpha = 0.9;

    // (c) 로고 원 — 흰 바탕 로고 + 산업색 림. 아틀라스는 캔버스 좌표(y 아래)라 v 를 뒤집어 읽는다
    float lr = length(vQ - vec2(0.5, 0.5));
    if (lr < 0.42 + aa) {
      vec2 luv = (vQ - 0.08) / 0.84;
      vec3 logo = texture2D(uAtlas, vCell + vec2(luv.x, 1.0 - luv.y) * uCell).rgb;
      logo = mix(logo, vColor, smoothstep(0.36 - aa, 0.36 + aa, lr));
      float inside = 1.0 - smoothstep(0.42 - aa, 0.42 + aa, lr);
      col = mix(col, logo, inside);
      alpha = mix(alpha, 1.0, inside);
    }

    // (d) 역할 태그 — 유형색 작은 캡슐 + 어두운 글자 (aRole.w > 0 일 때만)
    float roleW = vRole.w > 0.0 ? (vRole.z / vRole.w) * 0.8 : 0.0;
    if (roleW > 0.0) {
      float x0 = 1.0;
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

    // (e) 기업명 — 남은 폭에 이름 셀을 맞춰 넣고, 접힐 때는 폭과 함께 옅어진다
    float nameX = 1.0 + roleW + (roleW > 0.0 ? 0.15 : 0.0);
    float nameW = vAspect - 0.3 - nameX;
    if (vName.w > 0.0 && vNameOn > 0.01 && nameW > 0.02 && vQ.x >= nameX && vQ.x <= nameX + nameW) {
      float nu = (vQ.x - nameX) / nameW;
      float ny = (vQ.y - 0.1) / 0.8;
      if (ny >= 0.0 && ny <= 1.0) {
        vec4 tx = texture2D(uNames, vec2(vName.x + nu * vName.z, vName.y + (1.0 - ny) * vName.w));
        col = mix(col, tx.rgb, tx.a * vNameOn);
      }
    }

    col *= dim;
    gl_FragColor = vec4(col, mask * alpha * vAlpha);
  }
`;

/**
 * 칩 피킹 — uv(레이캐스트 평면 좌표)가 캡슐 안인지. 스템과 투명한 모서리는 제외한다.
 * 셰이더의 (b) 와 같은 식이다.
 */
export function chipHit(aspect: number, uv: { x: number; y: number }): boolean {
  if (!(aspect > 0)) return false;
  const qx = uv.x * aspect;
  const qy = uv.y * CHIP_H - CHIP_STEM;
  if (qy < 0) return false;
  const cx = Math.min(Math.max(qx, 0.5), aspect - 0.5);
  return Math.hypot(qx - cx, qy - 0.5) <= 0.5;
}

import { useEffect, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { useGalaxy } from "@/store/galaxy";
import { useUi } from "@/store/ui";
import { codeOf, focusScrolls, isTyping, modalOpen } from "./keyboard";

/** 방향키 회전 각속도(rad/s)와 Shift 가속 배율 — 한 바퀴에 약 5초, Shift 로 2초 남짓 */
const TURN = 1.25;
const BOOST = 2.4;
/** W A S D 이동 속도 — 궤도 거리에 비례(초당 거리의 1/4)해 가까이서는 섬세하게, 멀리서는 크게 움직인다. 기본 프레이밍(거리 약 250~290)에서
    초당 60~70 유닛. 아래·위 한계는 월드 단위/초 */
const PAN = 0.25;
const PAN_MIN = 10;
const PAN_MAX = 150;
/** 궤도 중심이 원반 평면에서 벗어날 수 있는 반지름의 기본값 — Scene 이 모델 바깥 반지름(extent.maxR)의 1.4배를 넘겨 주고, 모델이 없을 때만 쓴다 (은하 R_MAX 175 기준) */
const PAN_BOUND = 250;
/** 경계의 바닥 — 이웃이 없는 기업 뷰는 extent.maxR 이 0 이라 그대로 쓰면 W A S D 가 한 발도 못 움직인다. 기업 뷰 카메라 거리(약 50) 남짓은 열어 둔다 */
const PAN_BOUND_MIN = 60;
/** 극각 하한의 절대 바닥 — OrbitControls 의 makeSafe 와 같은 값. 정확히 0 이면 카메라가 y 축 위에 올라서 lookAt(up=+y) 이 무너진다 */
const EPS = 1e-6;

const ARROWS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);
const MOVES = new Set(["KeyW", "KeyA", "KeyS", "KeyD"]);
const OFFSET = new THREE.Vector3();
const SPH = new THREE.Spherical();
const FWD = new THREE.Vector3();
const RIGHT = new THREE.Vector3();
const UP = new THREE.Vector3(0, 1, 0);
const WISH = new THREE.Vector3();
const NEXT = new THREE.Vector3();
const STEP = new THREE.Vector3();
const RAD = new THREE.Vector3();

/** three OrbitControls 중 여기서 쓰는 면 — drei 가 makeDefault 로 올려 둔 인스턴스를 useThree(controls) 로 받는다 */
interface Orbit {
  target: THREE.Vector3;
  enabled: boolean;
  minPolarAngle: number;
  maxPolarAngle: number;
}

/**
 * 궤도 카메라의 키보드 조작.
 *  - ← → 는 은하 둘레를 돌고(방위각), ↑ ↓ 는 내려다보는 각도(극각)를 바꾼다. W S 는 시선 방향(원반 평면에 눕힌)으로 앞뒤,
 *    A D 는 좌우로 궤도 중심을 옮긴다 — 카메라도 같은 만큼 따라가므로 옮긴 자리를 중심으로 계속 돌려볼 수 있다. Shift 로 둘 다 가속
 *  - OrbitControls 자체의 키 입력(listenToKeyEvents)은 keydown 반복에 의존해 끊기고, 수식키 없는 방향키는 팬(꺼져 있음)이라
 *    여기서 프레임마다 속도로 움직인다. 관성(0.002^dt 로 수렴)으로 시작·정지가 부드럽다
 *  - 카메라 위치(와 target)만 옮기고 시선을 target 으로 다시 맞춘다. controls.update() 는 부르지 않는다 — drei 가 프레임 첫머리
 *    (priority -1)에 이미 불렀고, 한 번 더 부르면 그 프레임의 드래그 감쇠가 두 번 진행돼 드래그 관성이 반으로 꺾인다. 다음 프레임의
 *    update() 가 이 위치에서 구면좌표를 다시 읽으므로 드래그 회전과 자연히 합쳐진다 (Director 의 연출 카메라와 같은 방식)
 *  - 극각 한계는 OrbitControls 의 min/maxPolarAngle 을 그대로 읽는다. 한계에 닿으면 관성만 끊고, 카메라를 한계 안으로 밀어 넣지는
 *    않는다 — 드래그가 남긴 자세를 방향키가 건드리면 첫 프레임에 튀기 때문. 이동도 bound 에 닿으면 바깥으로 미는 성분만 끊어 가장자리를 따라 미끄러진다
 *  - 방향은 three 의 기본 키 규약을 따른다: ← 는 카메라가 왼쪽으로 돌아가고(장면은 오른쪽으로 흐름), ↑ 는 더 위에서 내려다본다
 * 카메라가 잠긴 동안(워프·인트로·재프레이밍·행성 드래그 — 판정은 Director 가 controls.enabled 한 곳에 쓴다), 검색 팔레트가 열려 있을 때,
 * 모달이 떠 있을 때, 포커스가 스크롤 가능한 HUD(우측 패널 목록) 안에 있을 때(방향키만)는 받지 않는다.
 */
/** @param bound 궤도 중심이 원점에서 벗어날 수 있는 평면 반지름 — 가장자리를 바깥에서 볼 여유만 두고 빈 우주로 나가지 않게 */
export default function OrbitKeys({ bound = PAN_BOUND }: { bound?: number }) {
  const camera = useThree((s) => s.camera);
  const controls = useThree((s) => s.controls) as unknown as Orbit | null;

  // 렌더 중에는 .current 를 읽지 않는다(react-hooks/refs) — 이펙트·프레임 안에서만 꺼내 쓴다
  const stRef = useRef({
    keys: new Set<string>(),
    /** x = 방위각(theta) 각속도, y = 극각(phi) 각속도 */
    vel: new THREE.Vector2(),
    /** W A S D 이동 속도(월드 단위/초, 원반 평면) */
    pan: new THREE.Vector3(),
    /** Shift 가속 — 키 집합이 아니라 매 키 이벤트의 e.shiftKey 로 갱신한다. 집합은 여러 곳에서 통째로 비워지는데 Shift 는 자동 반복이 없어 다시 채워지지 않기 때문 */
    shift: false,
  });

  useEffect(() => {
    const st = stRef.current;
    const clear = () => st.keys.clear();
    const onKeyDown = (e: KeyboardEvent) => {
      st.shift = e.shiftKey;
      // macOS 는 Cmd 가 눌린 동안 다른 키의 keyup 을 보내지 않는다 — Cmd 조합이 시작되면 누르고 있던 키를 전부 잊어 회전이 걸리지 않게
      if (e.metaKey) {
        clear();
        return;
      }
      const code = codeOf(e);
      const arrow = ARROWS.has(code);
      if (!arrow && !MOVES.has(code)) return;
      // Alt+← 는 브라우저 뒤로가기, Ctrl+방향키·Ctrl+W/S 는 OS·브라우저 단축키 — 가로채지 않는다
      if (e.altKey || e.ctrlKey) return;
      const s = useGalaxy.getState();
      // 모달(로그인 등)이 떠 있으면 그 안의 버튼에 포커스가 있어도 뒤의 은하를 움직이지 않는다
      // 상단 산업 선택판이 열려 있을 때도 마찬가지 — 판을 보는 중에 방향키가 뒤의 은하를 돌리지 않게.
      // `?` 도움말 허브는 비모달(aria-modal 없음)이라 modalOpen() 에 잡히지 않으므로 따로 본다 — 매뉴얼을 읽는 중에 WASD·←→ 가 은하를 돌리면 안 된다
      if (s.searchOpen || s.industryOpen || useUi.getState().helpOpen || isTyping() || modalOpen()) return;
      // 패널 목록 안에 포커스가 있으면 방향키는 그 목록의 스크롤이다 (W A S D 에는 기본 동작이 없어 그대로 받는다)
      if (arrow && focusScrolls(code === "ArrowUp" || code === "ArrowDown" ? "y" : "x")) return;
      st.keys.add(code);
      // 문서·다른 HUD 가 방향키로 같이 스크롤되지 않게
      if (arrow) e.preventDefault();
    };
    const onKeyUp = (e: KeyboardEvent) => {
      st.shift = e.shiftKey;
      if (e.metaKey || e.key === "Meta") clear();
      else st.keys.delete(codeOf(e));
    };
    // 창이 포커스를 잃거나(blur), 탭이 숨거나, 네이티브 컨텍스트 메뉴가 열리면 keyup 이 페이지에 오지 않는다 — 그때 누르던 키는 놓은 것으로 본다
    const onHidden = () => {
      if (document.visibilityState === "hidden") clear();
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", clear);
    window.addEventListener("contextmenu", clear);
    document.addEventListener("visibilitychange", onHidden);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", clear);
      window.removeEventListener("contextmenu", clear);
      document.removeEventListener("visibilitychange", onHidden);
    };
  }, []);

  useFrame((_, delta) => {
    const s = useGalaxy.getState();
    if (!controls) return;
    const st = stRef.current;
    const dt = Math.min(delta, 0.05);
    // 카메라가 잠겨 있으면(Director 가 워프·연출·행성 드래그 중 enabled 를 내린다) 관성만 끊고 키는 기억한다 — 워프가 끝나면 이어서 움직인다.
    // 검색 팔레트는 ↑↓ 를 결과 이동에 쓰므로 눌린 키도 잊는다
    if (!controls.enabled || s.searchOpen) {
      if (s.searchOpen) st.keys.clear();
      st.vel.set(0, 0);
      st.pan.set(0, 0, 0);
      return;
    }
    const k = st.keys;
    const boost = st.shift ? BOOST : 1;
    // 관성: 목표 속도로 부드럽게 수렴
    const damp = 1 - Math.pow(0.002, dt);

    // 회전 의도
    let wx = 0;
    let wy = 0;
    if (k.has("ArrowLeft")) wx -= 1;
    if (k.has("ArrowRight")) wx += 1;
    if (k.has("ArrowUp")) wy -= 1; // phi 가 줄수록 위로 올라간다
    if (k.has("ArrowDown")) wy += 1;
    st.vel.x += (wx * TURN * boost - st.vel.x) * damp;
    st.vel.y += (wy * TURN * boost - st.vel.y) * damp;

    // 이동 의도 — 시선을 원반 평면에 눕힌 방향이 앞(원반 위에서 보면 화면 위쪽). 정점 근처(minPolarAngle)에서도 평면 성분이 남아 방향이 정해진다
    FWD.copy(controls.target).sub(camera.position);
    FWD.y = 0;
    if (FWD.lengthSq() < 1e-8) FWD.set(0, 0, -1);
    else FWD.normalize();
    RIGHT.crossVectors(FWD, UP);
    WISH.set(0, 0, 0);
    if (k.has("KeyW")) WISH.add(FWD);
    if (k.has("KeyS")) WISH.sub(FWD);
    if (k.has("KeyD")) WISH.add(RIGHT);
    if (k.has("KeyA")) WISH.sub(RIGHT);
    if (WISH.lengthSq() > 0) {
      const speed = THREE.MathUtils.clamp(camera.position.distanceTo(controls.target) * PAN, PAN_MIN, PAN_MAX) * boost;
      WISH.normalize().multiplyScalar(speed);
    }
    st.pan.lerp(WISH, damp);

    const turning = Math.abs(st.vel.x) >= 1e-4 || Math.abs(st.vel.y) >= 1e-4;
    const moving = st.pan.lengthSq() >= 1e-6;
    if (!turning) st.vel.set(0, 0);
    if (!moving) st.pan.set(0, 0, 0);
    if (!turning && !moving) return;

    if (turning) {
      // 카메라 up 은 기본(+y)이라 OrbitControls 와 같은 구면좌표계다
      OFFSET.copy(camera.position).sub(controls.target);
      SPH.setFromVector3(OFFSET);
      SPH.theta += st.vel.x * dt;
      // 한계는 "지금 각도"까지 넓혀 잡는다 — 한계 밖에 있던 카메라를 안으로 끌어오지 않고, 더 밖으로 가는 것만 막는다
      const lo = Math.min(Math.max(controls.minPolarAngle, EPS), SPH.phi);
      const hi = Math.max(Math.min(controls.maxPolarAngle, Math.PI - EPS), SPH.phi);
      const phi = SPH.phi + st.vel.y * dt;
      SPH.phi = THREE.MathUtils.clamp(phi, lo, hi);
      // 한계에 닿으면 관성을 끊어 벽에 붙어 미는 느낌을 없앤다
      if (SPH.phi !== phi) st.vel.y = 0;
      camera.position.copy(controls.target).add(OFFSET.setFromSpherical(SPH));
    }
    if (moving) {
      // 궤도 중심과 카메라를 같은 벡터로 평행이동한다 — 거리·각도는 그대로라 시선만 옮겨 간다
      NEXT.copy(controls.target).addScaledVector(st.pan, dt);
      // 경계도 "지금 반지름"까지 넓혀 잡는다 — 모델이 바뀌어 경계가 줄어도 안으로 끌어오지 않고, 더 밖으로 가는 것만 막는다
      const limit = Math.max(bound, PAN_BOUND_MIN, Math.hypot(controls.target.x, controls.target.z));
      const r = Math.hypot(NEXT.x, NEXT.z);
      if (r > limit) {
        NEXT.x *= limit / r;
        NEXT.z *= limit / r;
        // 경계를 밀어내는(바깥 방향) 속도 성분만 끊는다 — 접선 성분은 남겨 가장자리를 따라 제속도로 미끄러진다 (극각 한계에서 phi 만 끊는 것과 같은 규칙)
        RAD.set(NEXT.x, 0, NEXT.z).normalize();
        const outward = st.pan.dot(RAD);
        if (outward > 0) st.pan.addScaledVector(RAD, -outward);
      }
      STEP.copy(NEXT).sub(controls.target);
      controls.target.copy(NEXT);
      camera.position.add(STEP);
    }
    camera.lookAt(controls.target);
  });

  return null;
}

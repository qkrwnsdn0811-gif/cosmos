import { useEffect, useMemo, useRef } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, AdaptiveDpr, Preload } from '@react-three/drei';
import {
  EffectComposer,
  Bloom,
  ChromaticAberration,
  Vignette,
  Noise,
} from '@react-three/postprocessing';
import { BlendFunction } from 'postprocessing';
import * as THREE from 'three';

import Starfield from './Starfield';
import Nebula from './Nebula';
import Warp from './Warp';
import GalaxyView from './GalaxyView';
import SystemView from './SystemView';
import { useGalaxy } from '../store/useGalaxy';
import { NODE_POS, N_DAYS } from '../data/universe';

/* 각 단계의 카메라 자세 */
const POSE = {
  intro: { pos: [0, 52, 250], tgt: [0, 0, 0] },
  galaxy: { pos: [0, 38, 142], tgt: [0, 0, 0] },
  system: { pos: [0, 27, 118], tgt: [0, -12, 0] },
};

const WARP_COMMIT = 0.6; // 씬을 바꿔치기하는 시점 (초)
const WARP_END = 1.45;

const easeOutCubic = (x) => 1 - Math.pow(1 - x, 3);
const easeInQuad = (x) => x * x;
const smoothstep = (a, b, x) => {
  const t = THREE.MathUtils.clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};

/**
 * 카메라 연출 + 워프 타이밍을 모두 담당하는 디렉터.
 * 스토어를 구독하지 않고 getState() 로 읽어 리렌더를 만들지 않는다.
 */
function Director({ warpRef }) {
  const camera = useThree((s) => s.camera);
  const controls = useThree((s) => s.controls);

  const rig = useRef({
    lastPhase: null,
    lastFocus: undefined,
    anim: null, // { t, dur, fromPos, fromTgt, toPos, toTgt }
    introAngle: 0,
    warpDir: new THREE.Vector3(),
    warpAim: new THREE.Vector3(),
    speed: 0,
  }).current;

  const startAnim = (toPose, dur, fromPos) => {
    const tgt = controls ? controls.target : new THREE.Vector3();
    rig.anim = {
      t: 0,
      dur,
      fromPos: (fromPos ?? camera.position).clone(),
      fromTgt: tgt.clone(),
      toPos: new THREE.Vector3().fromArray(toPose.pos),
      toTgt: new THREE.Vector3().fromArray(toPose.tgt),
    };
    if (fromPos) camera.position.copy(fromPos);
  };

  useFrame((state, delta) => {
    const dt = Math.min(delta, 0.05);
    const s = useGalaxy.getState();
    const now = performance.now();

    /* ---------- 워프 타이밍 ---------- */
    let intensity = 0;
    if (s.phase === 'warp') {
      const el = (now - s.warpStartedAt) / 1000;
      intensity =
        smoothstep(0, 0.22, el) * (1 - smoothstep(WARP_END - 0.6, WARP_END, el));

      if (el < WARP_COMMIT) {
        // 1단계 — 목표를 향해 가속하며 돌진
        if (rig.speed === 0) {
          const aim = s.warpTarget && NODE_POS[s.warpTarget];
          if (aim && s.focus === null) rig.warpAim.fromArray(aim);
          else rig.warpAim.set(0, 0, 0);
          rig.warpDir.copy(rig.warpAim).sub(camera.position).normalize();
        }
        rig.speed = THREE.MathUtils.lerp(rig.speed, 140, dt * 3.2);
        camera.position.addScaledVector(rig.warpDir, rig.speed * dt * easeInQuad(el / WARP_COMMIT));
        if (controls) {
          controls.target.lerp(rig.warpAim, dt * 3);
          controls.update();
        }
      } else if (!s.warpCommitted) {
        // 2단계 — 터널이 화면을 덮은 사이 씬 교체 + 카메라 재배치
        s.commitWarp();
        const pose = s.warpTarget ? POSE.system : POSE.galaxy;
        const from = new THREE.Vector3().fromArray(pose.pos).multiplyScalar(1.75);
        if (controls) controls.target.fromArray(pose.tgt);
        startAnim(pose, WARP_END - WARP_COMMIT - 0.05, from);
        rig.speed = 0;
        rig.lastPhase = 'warp-committed';
      }

      if (el >= WARP_END) {
        s.completeWarp();
        rig.lastPhase = s.warpTarget ? 'system' : 'galaxy';
        rig.lastFocus = s.focus;
      }
    } else {
      rig.speed = 0;
      /* ---------- 단계 진입 시 1회 카메라 이동 ---------- */
      if (rig.lastPhase !== s.phase) {
        if (rig.lastPhase !== 'warp-committed') {
          startAnim(POSE[s.phase] ?? POSE.galaxy, s.phase === 'intro' ? 0.01 : 1.4);
        }
        rig.lastPhase = s.phase;
      }
    }

    warpRef.current = intensity;

    /* ---------- 카메라 애니메이션 ---------- */
    if (rig.anim) {
      const a = rig.anim;
      a.t = Math.min(a.dur, a.t + dt);
      const k = easeOutCubic(a.t / a.dur);
      camera.position.lerpVectors(a.fromPos, a.toPos, k);
      if (controls) {
        controls.target.lerpVectors(a.fromTgt, a.toTgt, k);
        controls.update();
      }
      if (a.t >= a.dur) rig.anim = null;
    }

    /* ---------- 인트로: 천천히 회전 ---------- */
    if (s.phase === 'intro' && !rig.anim) {
      rig.introAngle += dt * 0.035;
      const r = 250;
      camera.position.set(
        Math.sin(rig.introAngle) * r,
        52 + Math.sin(rig.introAngle * 0.7) * 14,
        Math.cos(rig.introAngle) * r
      );
      if (controls) {
        controls.target.set(0, 0, 0);
        controls.update();
      }
    }

    /* ---------- 사용자 조작 허용 여부 ---------- */
    if (controls) {
      const free = (s.phase === 'galaxy' || s.phase === 'system') && !rig.anim;
      if (controls.enabled !== free) controls.enabled = free;
    }

    /* ---------- 타임라인 재생 ---------- */
    if (s.playing && s.phase !== 'warp') {
      const stepEvery = 0.045; // ≈ 22 영업일/초
      rig.playAcc = (rig.playAcc || 0) + dt;
      const steps = Math.floor(rig.playAcc / stepEvery);
      if (steps > 0) {
        rig.playAcc -= steps * stepEvery;
        const next = useGalaxy.getState().day + steps;
        if (next >= N_DAYS - 1) {
          s.setDay(N_DAYS - 1);
          s.setPlaying(false);
        } else {
          s.setDay(next);
        }
      }
    } else {
      rig.playAcc = 0;
    }
  });

  useEffect(() => {
    camera.position.set(...POSE.intro.pos);
  }, [camera]);

  return null;
}

/** 워프 강도에 따라 색수차를 키우는 포스트프로세싱 */
function Effects({ warpRef }) {
  const ca = useRef();
  const offset = useMemo(() => new THREE.Vector2(0.0006, 0.0004), []);
  useFrame(() => {
    if (!ca.current) return;
    const k = warpRef.current;
    ca.current.offset.set(0.0005 + k * 0.0028, 0.0004 + k * 0.0018);
  });
  return (
    <EffectComposer disableNormalPass multisampling={0}>
      <Bloom
        intensity={1.5}
        luminanceThreshold={0.42}
        luminanceSmoothing={0.3}
        mipmapBlur
        radius={0.66}
      />
      <ChromaticAberration ref={ca} offset={offset} radialModulation modulationOffset={0.3} />
      <Noise opacity={0.035} blendFunction={BlendFunction.OVERLAY} />
      <Vignette eskil={false} offset={0.24} darkness={0.86} />
    </EffectComposer>
  );
}

/** 개발 모드에서 씬 상태를 콘솔/자동화로 들여다보기 위한 브리지 */
function DevBridge() {
  const get = useThree((s) => s.get);
  useEffect(() => {
    if (import.meta.env.DEV) window.__r3f = get;
  }, [get]);
  return null;
}

function Content({ warpRef }) {
  const focus = useGalaxy((s) => s.focus);
  const clearSelection = useGalaxy((s) => s.clearSelection);
  const setHovered = useGalaxy((s) => s.setHovered);

  return (
    <>
      <DevBridge />
      <Director warpRef={warpRef} />
      <OrbitControls
        makeDefault
        enablePan={false}
        enableDamping
        dampingFactor={0.06}
        rotateSpeed={0.45}
        zoomSpeed={0.7}
        minDistance={26}
        maxDistance={320}
      />

      <fog attach="fog" args={['#03050c', 260, 900]} />
      <Nebula />
      <Starfield />

      <group
        onPointerMissed={() => {
          clearSelection();
          setHovered(null);
        }}
      >
        {focus === null ? <GalaxyView /> : <SystemView />}
      </group>

      <Warp intensityRef={warpRef} />
      <Effects warpRef={warpRef} />
      <AdaptiveDpr />
      <Preload all />
    </>
  );
}

export default function Scene() {
  const warpRef = useRef(0);
  return (
    <Canvas
      dpr={[1, 1.8]}
      gl={{ antialias: true, powerPreference: 'high-performance' }}
      camera={{ fov: 55, near: 0.5, far: 3000, position: POSE.intro.pos }}
      onCreated={({ gl }) => {
        gl.toneMapping = THREE.ACESFilmicToneMapping;
        gl.toneMappingExposure = 1.05;
        gl.setClearColor('#03050c', 1); // 성운 큐브맵이 구워지기 전 배경
      }}
    >
      <Content warpRef={warpRef} />
    </Canvas>
  );
}

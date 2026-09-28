import { useMemo, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';

const SEGS = 30; // 곡선당 분할 수

/**
 * 관계 간선을 하나의 메시로 그린다.
 *
 * - 2차 베지어 곡선으로 아크를 만들고
 * - 정점 셰이더에서 카메라를 향해 빌보드된 리본으로 확장해 "화면 기준 두께"를 갖게 하고
 *   (일반 gl.LINES 는 대부분의 플랫폼에서 1px 로 고정되어 우주 공간에서 거의 보이지 않는다)
 * - 프래그먼트 셰이더에서 간선을 따라 흐르는 펄스를 그려 방향성과 생동감을 준다.
 *
 * edges: [{ key, from:[x,y,z], to:[x,y,z], color:'#hex', alpha:0..1, seed?:number }]
 */
export default function RelationLines({ edges, bow = 0.18, width = 2.4 }) {
  const mat = useRef();
  const size = useThree((s) => s.size);
  const camera = useThree((s) => s.camera);

  const geometry = useMemo(() => {
    const n = edges.length;
    const pts = SEGS + 1;
    const vCount = n * pts * 2;

    const position = new Float32Array(vCount * 3);
    const aDir = new Float32Array(vCount * 3);
    const aColor = new Float32Array(vCount * 3);
    const aAlpha = new Float32Array(vCount);
    const aT = new Float32Array(vCount);
    const aSeed = new Float32Array(vCount);
    const aSide = new Float32Array(vCount);
    const index = new Uint32Array(n * SEGS * 6);

    const A = new THREE.Vector3();
    const B = new THREE.Vector3();
    const C = new THREE.Vector3();
    const out = new THREE.Vector3();
    const col = new THREE.Color();
    const curve = [];
    let vi = 0;
    let ii = 0;

    edges.forEach((e, ei) => {
      A.fromArray(e.from);
      B.fromArray(e.to);
      const dist = A.distanceTo(B);

      // 중점을 원점 바깥으로 밀어 아크를 만든다
      C.addVectors(A, B).multiplyScalar(0.5);
      out.copy(C);
      if (out.lengthSq() < 1e-6) out.set(0, 1, 0);
      out.normalize().multiplyScalar(dist * bow);
      out.y += dist * bow * 0.3;
      C.add(out);

      curve.length = 0;
      for (let s = 0; s <= SEGS; s++) {
        const t = s / SEGS;
        const it = 1 - t;
        curve.push(
          new THREE.Vector3()
            .addScaledVector(A, it * it)
            .addScaledVector(C, 2 * it * t)
            .addScaledVector(B, t * t)
        );
      }

      col.set(e.color);
      const seed = e.seed ?? (ei * 0.6180339887) % 1;
      const base = vi;

      for (let s = 0; s <= SEGS; s++) {
        const p = curve[s];
        const prev = curve[Math.max(0, s - 1)];
        const next = curve[Math.min(SEGS, s + 1)];
        const dx = next.x - prev.x;
        const dy = next.y - prev.y;
        const dz = next.z - prev.z;
        const len = Math.hypot(dx, dy, dz) || 1;
        const t = s / SEGS;

        for (let k = 0; k < 2; k++) {
          position[vi * 3] = p.x;
          position[vi * 3 + 1] = p.y;
          position[vi * 3 + 2] = p.z;
          aDir[vi * 3] = dx / len;
          aDir[vi * 3 + 1] = dy / len;
          aDir[vi * 3 + 2] = dz / len;
          aColor[vi * 3] = col.r;
          aColor[vi * 3 + 1] = col.g;
          aColor[vi * 3 + 2] = col.b;
          aAlpha[vi] = e.alpha;
          aT[vi] = t;
          aSeed[vi] = seed;
          aSide[vi] = k === 0 ? 1 : -1;
          vi++;
        }
      }

      for (let s = 0; s < SEGS; s++) {
        const a = base + s * 2;
        index[ii++] = a;
        index[ii++] = a + 1;
        index[ii++] = a + 2;
        index[ii++] = a + 1;
        index[ii++] = a + 3;
        index[ii++] = a + 2;
      }
    });

    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(position, 3));
    g.setAttribute('aDir', new THREE.BufferAttribute(aDir, 3));
    g.setAttribute('aColor', new THREE.BufferAttribute(aColor, 3));
    g.setAttribute('aAlpha', new THREE.BufferAttribute(aAlpha, 1));
    g.setAttribute('aT', new THREE.BufferAttribute(aT, 1));
    g.setAttribute('aSeed', new THREE.BufferAttribute(aSeed, 1));
    g.setAttribute('aSide', new THREE.BufferAttribute(aSide, 1));
    g.setIndex(new THREE.BufferAttribute(index, 1));
    g.computeBoundingSphere();
    return g;
  }, [edges, bow]);

  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uWidth: { value: width },
      uPixel: { value: 0.002 },
    }),
    [width]
  );

  useFrame((state) => {
    if (!mat.current) return;
    mat.current.uniforms.uTime.value = state.clock.elapsedTime;
    // 화면상의 굵기를 일정하게 유지하기 위한 픽셀→월드 환산 계수
    const fov = (camera.fov * Math.PI) / 180;
    mat.current.uniforms.uPixel.value = (2 * Math.tan(fov / 2)) / Math.max(1, size.height);
  });

  return (
    <mesh geometry={geometry} frustumCulled={false} raycast={() => null}>
      <shaderMaterial
        ref={mat}
        uniforms={uniforms}
        transparent
        depthWrite={false}
        side={THREE.DoubleSide}
        blending={THREE.AdditiveBlending}
        toneMapped={false}
        vertexShader={/* glsl */ `
          attribute vec3 aDir;
          attribute vec3 aColor;
          attribute float aAlpha;
          attribute float aT;
          attribute float aSeed;
          attribute float aSide;

          uniform float uWidth;
          uniform float uPixel;

          varying vec3 vColor;
          varying float vAlpha;
          varying float vT;
          varying float vSeed;
          varying float vSide;

          void main(){
            vColor = aColor;
            vAlpha = aAlpha;
            vT = aT;
            vSeed = aSeed;
            vSide = aSide;

            vec4 mv = modelViewMatrix * vec4(position, 1.0);
            vec3 tan = normalize((modelViewMatrix * vec4(aDir, 0.0)).xyz);
            vec3 viewDir = normalize(-mv.xyz);
            vec3 side = cross(tan, viewDir);
            float l = length(side);
            side = l > 0.0001 ? side / l : vec3(1.0, 0.0, 0.0);

            // 화면 기준 굵기 → 뷰 공간 오프셋
            mv.xyz += side * aSide * uWidth * 0.5 * uPixel * (-mv.z);
            gl_Position = projectionMatrix * mv;
          }
        `}
        fragmentShader={/* glsl */ `
          uniform float uTime;
          varying vec3 vColor;
          varying float vAlpha;
          varying float vT;
          varying float vSeed;
          varying float vSide;

          void main(){
            if (vAlpha <= 0.002) discard;
            // 리본 가장자리를 부드럽게 (안티에일리어싱 대용)
            float edge = 1.0 - abs(vSide);
            float soft = smoothstep(0.0, 0.55, edge) * 0.55 + 0.45;

            // 간선을 따라 흐르는 펄스
            float head = fract(uTime * 0.17 + vSeed);
            float d = abs(fract(vT - head + 0.5) - 0.5);
            float pulse = exp(-d * d * 160.0);

            vec3 col = vColor * (0.55 + 2.9 * pulse);
            gl_FragColor = vec4(col * soft, vAlpha * soft * (0.5 + 0.5 * pulse));
          }
        `}
      />
    </mesh>
  );
}

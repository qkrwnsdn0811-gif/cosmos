import { useEffect } from 'react';
import { useThree } from '@react-three/fiber';
import * as THREE from 'three';

const vert = /* glsl */ `
  varying vec3 vDir;
  void main(){
    vDir = normalize(position);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const frag = /* glsl */ `
  varying vec3 vDir;

  float hash(vec3 p){
    p = fract(p * 0.3183099 + vec3(0.1, 0.2, 0.3));
    p *= 17.0;
    return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
  }
  float noise(vec3 x){
    vec3 i = floor(x);
    vec3 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(mix(hash(i + vec3(0,0,0)), hash(i + vec3(1,0,0)), f.x),
          mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
      mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x),
          mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
  }
  float fbm(vec3 p){
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 5; i++){ v += a * noise(p); p *= 2.07; a *= 0.5; }
    return v;
  }

  void main(){
    vec3 d = vDir;
    float n = fbm(d * 2.1);
    float m = fbm(d * 5.3 + n * 1.4);

    // 은하수 띠 — 특정 평면 근처에 밀도를 몰아준다
    float band = exp(-pow(abs(d.y * 2.6 + n * 0.55 - 0.28), 2.0) * 3.2);

    vec3 deepBlue = vec3(0.035, 0.075, 0.20);
    vec3 teal     = vec3(0.06, 0.32, 0.46);
    vec3 violet   = vec3(0.26, 0.12, 0.44);
    vec3 amber    = vec3(0.62, 0.32, 0.10);

    vec3 col = deepBlue * 0.5;
    col = mix(col, violet, smoothstep(0.38, 0.92, n) * 0.7);
    col = mix(col, teal, smoothstep(0.48, 0.95, m) * 0.5);
    col += amber * pow(max(m - 0.62, 0.0), 2.0) * 1.1;

    // 전경(그래프)을 덮지 않도록 전체 밝기를 크게 낮춘다
    // 큐브맵(8bit)에 굽고 톤매핑을 거치면 어두워지므로 미리 스케일을 올려 굽는다
    col *= (0.09 + band * 0.42) * 2.8;

    // 아주 미세한 그레인으로 밴딩 제거
    col += (hash(d * 900.0) - 0.5) * 0.012;

    gl_FragColor = vec4(col, 1.0);
  }
`;

/**
 * 성운 스카이돔.
 *
 * fbm 5옥타브를 매 프레임 풀스크린으로 돌리면 프레임 예산의 10% 이상을 먹는다.
 * 카메라만 움직이고 성운 자체는 변하지 않으므로 **기동 시 큐브맵에 한 번만 굽고**
 * 그 결과를 scene.background 로 쓴다. 이후 렌더 비용은 사실상 0.
 */
export default function Nebula() {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);

  useEffect(() => {
    const rt = new THREE.WebGLCubeRenderTarget(1024, {
      generateMipmaps: true,
      minFilter: THREE.LinearMipmapLinearFilter,
      magFilter: THREE.LinearFilter,
    });
    const cam = new THREE.CubeCamera(1, 4000, rt);

    const geo = new THREE.SphereGeometry(900, 64, 40);
    const mat = new THREE.ShaderMaterial({
      vertexShader: vert,
      fragmentShader: frag,
      side: THREE.BackSide,
      depthWrite: false,
      toneMapped: false,
    });
    const bakeScene = new THREE.Scene();
    bakeScene.add(new THREE.Mesh(geo, mat));

    // 톤매핑이 두 번 적용되지 않도록 굽는 동안만 끈다
    const prevTone = gl.toneMapping;
    gl.toneMapping = THREE.NoToneMapping;
    cam.update(gl, bakeScene);
    gl.toneMapping = prevTone;

    const prevBg = scene.background;
    scene.background = rt.texture;

    geo.dispose();
    mat.dispose();

    return () => {
      scene.background = prevBg;
      rt.dispose();
    };
  }, [gl, scene]);

  return null;
}

import type * as THREE from "three";

/**
 * 속성 버퍼를 다음 프레임에 다시 올리도록 표시한다.
 * useMemo 로 만든 지오메트리 속성에 useEffect 안에서 `attr.needsUpdate = true` 를 직접 쓰면 react-hooks/immutability
 * (메모 값 변경 금지) 에 걸린다. three 의 needsUpdate 는 렌더와 무관한 GPU 업로드 플래그이므로 이 헬퍼로 의도를 드러내고 규칙을 피한다.
 */
export function touch(...attrs: (THREE.BufferAttribute | THREE.InterleavedBufferAttribute | THREE.InstancedBufferAttribute)[]) {
  for (const a of attrs) a.needsUpdate = true;
}

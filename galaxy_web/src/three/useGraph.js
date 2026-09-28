import { useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useGalaxy } from '../store/useGalaxy';
import { buildGraph } from '../data/universe';

/**
 * 현재 시점 + 필터로 관계 그래프를 계산한다.
 * 3D 뷰와 우측 패널이 같은 결과를 봐야 하므로 훅으로 공유한다.
 * (실 API 연동 시 여기만 useQuery 로 바꾸면 된다)
 */
export function useGraph() {
  const { day, threshold, activeTypes, onlySerendipity } = useGalaxy(
    useShallow((s) => ({
      day: s.day,
      threshold: s.threshold,
      activeTypes: s.activeTypes,
      onlySerendipity: s.onlySerendipity,
    }))
  );
  const typeKey = [...activeTypes].sort().join(',');

  return useMemo(() => {
    let edges = buildGraph(day, { threshold, types: activeTypes });
    if (onlySerendipity) edges = edges.filter((e) => e.serendipity);
    return edges;
    // activeTypes 는 Set 이라 참조가 매번 바뀐다 → 직렬화한 typeKey 로 비교
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day, threshold, typeKey, onlySerendipity]);
}

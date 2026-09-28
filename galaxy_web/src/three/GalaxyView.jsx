import { useMemo } from 'react';
import CompanyNode from './CompanyNode';
import RelationLines from './RelationLines';
import LabelSprite from './LabelSprite';
import { useGraph } from './useGraph';
import { useGalaxy } from '../store/useGalaxy';
import {
  ALL_NEWS,
  COMPANIES,
  NODE_POS,
  REL_TYPES,
  SECTORS,
  SECTOR_CENTERS,
  SECTOR_LIST,
  nodeRadius,
} from '../data/universe';

/** 현재 시점 기준 최근 뉴스(12영업일 이내)를 기업별로 모은다 */
function useRecentNews(day) {
  return useMemo(() => {
    const map = {};
    for (const n of ALL_NEWS) {
      const age = day - n.day;
      if (age < 0 || age > 12) continue;
      const prev = map[n.companyId];
      if (!prev || age < prev.ageDays) map[n.companyId] = { tone: n.tone, ageDays: age, news: n };
    }
    return map;
  }, [day]);
}

export default function GalaxyView() {
  const day = useGalaxy((s) => s.day);
  const hovered = useGalaxy((s) => s.hovered);
  const selectedEdge = useGalaxy((s) => s.selectedEdge);
  const showLabels = useGalaxy((s) => s.showLabels);
  const setHovered = useGalaxy((s) => s.setHovered);
  const warpTo = useGalaxy((s) => s.warpTo);

  const edges = useGraph();
  const recentNews = useRecentNews(day);

  // 하이라이트 대상 계산
  const { lineEdges, connected } = useMemo(() => {
    const focusKey = selectedEdge;
    const connected = new Set();
    const lineEdges = edges.map((e) => {
      const touchesHover = hovered && (e.a === hovered || e.b === hovered);
      const isSelected = focusKey === e.key;
      if (touchesHover || isSelected) {
        connected.add(e.a);
        connected.add(e.b);
      }
      const base = 0.16 + Math.min(1, (Math.abs(e.corr) - 0.3) / 0.55) * 0.62;
      let alpha = base;
      if (focusKey) alpha = isSelected ? 1 : base * 0.12;
      else if (hovered) alpha = touchesHover ? 1 : base * 0.12;
      return {
        key: e.key,
        from: NODE_POS[e.a],
        to: NODE_POS[e.b],
        color: e.serendipity ? '#ff9de0' : REL_TYPES[e.type].color,
        alpha,
        seed: (parseInt(e.a, 10) * 31 + parseInt(e.b, 10)) % 1000 / 1000,
      };
    });
    return { lineEdges, connected };
  }, [edges, hovered, selectedEdge]);

  const anyHighlight = Boolean(hovered || selectedEdge);

  return (
    <group>
      <RelationLines edges={lineEdges} bow={0.16} />

      {/* 섹터 이름 (성단 라벨) */}
      {showLabels &&
        SECTOR_LIST.map((s) => {
          const c = SECTOR_CENTERS[s.id];
          if (!c) return null;
          return (
            <LabelSprite
              key={s.id}
              text={s.name}
              color={SECTORS[s.id].color}
              size={4.4}
              weight={700}
              opacity={0.34}
              position={[c[0], c[1] + 21, c[2]]}
            />
          );
        })}

      {COMPANIES.map((c) => {
        const isHot = hovered === c.id || connected.has(c.id);
        const dim = anyHighlight ? (isHot ? 1 : 0.22) : 1;
        return (
          <CompanyNode
            key={c.id}
            company={c}
            position={NODE_POS[c.id]}
            radius={nodeRadius(c)}
            dim={dim}
            active={hovered === c.id}
            news={recentNews[c.id] || null}
            showLabel={showLabels}
            onHover={setHovered}
            onClick={warpTo}
          />
        );
      })}
    </group>
  );
}

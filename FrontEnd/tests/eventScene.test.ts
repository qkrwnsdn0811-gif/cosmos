import { test } from "node:test";
import assert from "node:assert/strict";
import { chipName, eventScene, MENTION, NEWS_NODE, type EventNodeLike } from "../src/features/event/eventScene.ts";

const ar = (v: number | null) => [{ windowDays: 1, abnormalReturn: v }, { windowDays: 3, abnormalReturn: 0.5 }];
const step = (src: string, dst: string, type: string | null = "SUPPLY", strength: number | null = 0.8) => ({
  sourceCompanyId: src,
  targetCompanyId: dst,
  relationshipType: type,
  strength,
});
const node = (over: Partial<EventNodeLike> & Pick<EventNodeLike, "companyId" | "name">): EventNodeLike => ({
  basis: "INFERRED",
  hop: 1,
  status: "OK",
  anchorCompanyId: "sam",
  path: [],
  abnormalReturns: ar(null),
  ...over,
});

const nodes: EventNodeLike[] = [
  node({ companyId: "sam", name: "삼성전자", basis: "REPORTED", hop: 0, anchorCompanyId: null, abnormalReturns: ar(0.0065) }),
  node({ companyId: "dj", name: "동진쎄미켐", path: [step("dj", "sam")], abnormalReturns: ar(-0.048) }),
  node({ companyId: "hold", name: "월덱스", status: "HOLD", path: [step("hold", "sam", "SUPPLY", 0.3)] }),
  node({ companyId: "far", name: "먼회사", hop: 2, path: [step("dj", "sam"), step("far", "dj", "PARTNER")] }),
  node({ companyId: "lost", name: "끊긴회사", path: [step("lost", "nowhere", "COMENTION")] }),
  node({ companyId: null, name: "해외기업" }),
];

test("the article is the center and companies sit on orbits by how they were reached", () => {
  const { graph } = eventScene("ev1", "2026-10-14T00:00:00Z", nodes);
  assert.equal(graph.centerCompanyId, NEWS_NODE);
  const depth = Object.fromEntries(graph.nodes.map((n) => [n.companyId, n.depth]));
  assert.deepEqual(depth, { [NEWS_NODE]: 0, sam: 1, dj: 2, hold: 2, far: 3, lost: 2 });
});

test("lines: the article to each reported company, then every propagation step on the map", () => {
  const { graph } = eventScene("ev1", "2026-10-14T00:00:00Z", nodes);
  const lines = graph.edges.map((e) => `${e.sourceCompanyId}>${e.targetCompanyId}:${e.relationshipType}`).sort();
  assert.deepEqual(lines, [
    `${NEWS_NODE}>sam:${MENTION}`,
    "dj>sam:SUPPLY",
    "far>dj:PARTNER",
    "hold>sam:SUPPLY",
    "sam>lost:CO_MENTIONED",          // its path leaves the map, so it hangs from its anchor
  ].sort());
});

test("a held company's line is weaker than a kept one's", () => {
  const { graph } = eventScene("ev1", "2026-10-14T00:00:00Z", nodes);
  const score = (src: string) => graph.edges.find((e) => e.sourceCompanyId === src)!.score;
  assert.ok(score("hold") < score("dj"));
});

test("reported, held and day-one abnormal returns are kept for the scene", () => {
  const s = eventScene("ev1", "2026-10-14T00:00:00Z", nodes);
  assert.deepEqual([...s.reported], ["sam"]);
  assert.deepEqual([...s.held], ["hold"]);
  assert.deepEqual(Object.fromEntries(s.dayOne), { sam: 0.0065, dj: -0.048 });
});

test("chip names carry the day-one abnormal return", () => {
  assert.equal(chipName("SK하이닉스", -0.01689), "SK하이닉스 −1.7%");
  assert.equal(chipName("삼성전자", 0.00653418), "삼성전자 +0.7%");
  assert.equal(chipName("에이디테크놀로지주식회사", null), "에이디테크놀로지주…");
});

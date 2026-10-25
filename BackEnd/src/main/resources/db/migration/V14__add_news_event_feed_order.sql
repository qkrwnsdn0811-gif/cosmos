-- NEXUS 첫 화면 피드 순서 (2026-10-25): 날짜는 최신순, 같은 날 안에서는 기업을 돌아가며 보여 준다
-- (기업마다 그날 가장 최근 기사 → 두 번째 기사 …). 순서를 시각 하나로 담아 (시각, id) 커서를 그대로 쓴다.
-- 쓰기는 nexus cases 가 한다(nexus/nexus/cases/feed.py). 값이 없으면 dateline 으로 대신한다.
ALTER TABLE news_event ADD COLUMN feed_at TIMESTAMPTZ;
CREATE INDEX ix_news_event_chain_feed ON news_event (value_chain, (COALESCE(feed_at, dateline)) DESC, event_id DESC);

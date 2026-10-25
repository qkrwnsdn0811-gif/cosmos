-- NEXUS 첫 화면 피드 (2026-10-25): 기사의 주인공 기업(가장 비중 있게 나온 기업, 같으면 덜 자주 주인공인 기업).
-- 피드 순서(feed_at)를 정할 때 쓴 값이고, 화면은 기사의 기업 목록에서 이 기업을 맨 앞에 둔다. 쓰기는 nexus cases 가 한다.
ALTER TABLE news_event ADD COLUMN main_company_id UUID REFERENCES company (company_id);

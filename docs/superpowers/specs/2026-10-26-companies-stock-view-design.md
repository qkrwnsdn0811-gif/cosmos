# 기업 탭 주식 화면 — 설계 (2026-10-26)

기업 탭(`/companies`)을 관계 중심 목록에서 주식 시세판으로 바꾸고, 기업 상세 페이지(`/companies/:id`)를 새로 만든다.
관계 정보는 남기되 주가·지표가 먼저 보이게 한다. 11-03 발표에서 심사위원이 기업 탭을 열었을 때 투자 정보 화면으로
읽히는 것이 목표다. 마감(10-30) 안에 끝낼 크기로 자른다.

## 정한 것

| 항목 | 값 |
|---|---|
| 상세 위치 | 새 페이지 `/companies/:id` (사용자 선택). 목록 행을 누르면 이동한다 |
| 데이터 방식 | 백엔드에 시세 API 3개를 더한다. 계산은 SQL, 61종목이라 목록은 페이지 없이 한 번에 받는다 |
| 범위 | 주가가 있는 반도체 61종목(코스피·코스닥). 주가가 없는 기업은 목록에 남기고 시세 칸만 '-' |
| 주가 기준 | `stock_price_history` 일봉(1D)의 마지막 거래일. 지금은 2026-10-23 |
| 등락 기간 | 거래일 수로 센다: 1일 = 1거래일 전 종가 대비, 1개월 = 20거래일, 3개월 = 60거래일 |
| 관계 기준 | 공개 은하 스냅샷의 1년(1Y) 관계. 목록의 연결 관계 수와 상세의 관계 기업 목록이 같은 기준이다 |
| 원칙 | 관측된 숫자만 보여 준다. 오른다·내린다를 말하거나 매수·매도를 권하지 않는다 |
| 빼는 것 | 캔들 차트, 실시간 시세, 이동평균 같은 기술적 지표, 거래량 차트 |

## 1. 기업 목록 `/companies` — 시세판

- **시장 띠** (표 위)
  - 코스피·코스닥 카드: 종가, 전일 대비, 1개월 등락, 최근 3개월(60거래일) 미니 차트
  - 반도체 종목 카드: 마지막 거래일에 오른 종목 수 · 내린 종목 수 · 보합
  - "주가 2026-10-23 종가 기준"
- **표**: 기업(로고·이름·코드) | 현재가·1일 등락 | 1개월 추이(미니 차트, 21개 종가) | 1개월 등락 | 시가총액(규모 배지) |
  PER | PBR | 배당 | 연결 관계 수 | 관심
  - 적자 기업(PER 없음, 순이익 < 0)은 PER 칸에 '적자'
  - '최고 관계 점수' 열은 상세의 관계 기업 목록으로 옮긴다
- **정렬**: 시가총액(서버의 실제 값, 기본), 1일 등락, 1개월 등락, PER, 기업명
- **필터**: 검색어(이름·코드), 시장(코스피·코스닥 — 지금 들어 있는 나스닥은 뺀다), 산업
- '투자·지분 관계' 보기 전환은 그대로 둔다
- 행을 누르면 `/companies/:id` 로 간다. 관심 별표는 그대로

목록은 시세 API 하나로 그린다. 지금처럼 `/companies` 무한 스크롤 + 프론트의 예비 시가총액 표로 정렬하던 방식은 쓰지 않는다
(`lib/marketCapFallback.ts` 는 다른 곳에서 쓰지 않으면 지운다).

## 2. 기업 상세 `/companies/:id`

- **머리**: 로고, 이름, 코드, 시장, 업종, 현재가와 1일 등락, 기준일. 버튼 '관계 은하에서 보기'(`/galaxy?company=`)
- **주가 차트**
  - 기간 1M · 3M · 6M · 1Y (기본 3M)
  - 종목과 소속 시장 지수(코스피 종목은 코스피, 코스닥 종목은 코스닥)의 기간 누적 수익률 두 선. 날짜로 맞춘다
  - 마우스를 올리면 그날의 날짜·종가·종목 수익률·지수 수익률
  - 차트 아래: 기간 수익률, 지수 대비 차이(%p), 52주 최고·최저(1Y 주가로 계산), 기간 평균 거래대금
- **지표 카드**: 시가총액(순위·규모), PER, PBR, 배당수익률, 순이익(최근 4분기), 자본총계, 기준(재무 기준·주가 날짜)
  - PER·PBR 옆에 "반도체 61종목 중앙값"을 같이 적는다. 중앙값은 목록 시세 API 값으로 프론트에서 계산한다
    (PER은 양수만, PBR은 값이 있는 것만)
- **관계 기업 동향**
  - 요약: "관계 기업 n곳 · 1개월 상승 a · 하락 b · 평균 ±x%"
  - 관계 점수 순 목록: 로고·이름, 관계 종류, 관계 점수, 1개월 미니 차트, 1개월 등락, 1일 등락. 누르면 그 기업 상세로 간다
  - 관계 종류는 방향을 풀어 쓴다: 공급(상대가 나에게 공급 → '공급사', 내가 상대에게 → '고객사'), 투자(상대가 나에게 →
    '지분 보유사', 내가 상대에게 → '지분 투자처'), 협력 → '협력사', 경쟁 → '경쟁사', 함께 언급 → '함께 언급'
  - 같은 상대와 관계가 여러 개면 점수가 가장 높은 하나만 보여 주고 나머지 종류는 작은 글씨로 덧붙인다
- **최근 뉴스 이벤트**: 미리보기 창의 뉴스 이력 목록(`CompanyInvestSection` 의 과거 뉴스 부분)을 그대로 쓴다

## 3. 백엔드 API (새로 3개)

모두 공개 GET. `SecurityConfig.PUBLIC_GET_PATHS` 에 `/api/market/**` 를 더한다(`/api/companies/**` 는 이미 공개).

### `GET /api/market/indices?period=3M`

- period: 1M · 3M · 6M · 1Y (거래일 20 · 60 · 120 · 250개, 기준점 하나를 더 준다)
- 응답: `{ asOf, items: [{ indexCode, close, change1d, change1m, points: [{ date, close }] }] }` — KOSPI, KOSDAQ 순
- 출처: `market_index_price`

### `GET /api/companies/quotes`

- 페이지 없이 활성 기업 전부. 응답 `{ asOf, items: [CompanyQuote] }`
- `CompanyQuote`: companyId, name, stockCode, market, industry(대표 업종), close, tradingDate, change1d, change1m, change3m,
  spark(최근 21개 종가, 오래된 것부터), marketCap, marketCapRank, sizeClass, per, pbr, dividendYield, netIncomeTtm,
  relationCount(1Y)
- 주가가 없으면 시세 칸이 null, 재무가 없으면 재무 칸이 null
- 출처: `company`, `stock_price_history`(창 함수로 마지막 61개 종가), `company_fundamental`, `relationship_score_current`
  (공개 스냅샷, 1Y)

### `GET /api/companies/{id}/related`

- 응답 `{ companyId, window: "1Y", items: [{ relationType, direction, score, otherTypes[], quote: CompanyQuote }] }`
- direction: OUT(이 기업이 source) · IN(이 기업이 target) · NONE(방향 없는 관계)
- 점수 내림차순. 같은 상대는 최고 점수 하나로 묶는다(1번 절 규칙)
- 없는 기업 id는 404

상세 차트는 기존 `GET /api/companies/{id}/stock-prices?period=` 와 `GET /api/market/indices?period=` 를 겹쳐 그린다.

## 4. 프론트 구성

- `features/companies/`
  - `CompaniesPage.tsx` 다시 쓰기: 시장 띠 + 시세 표
  - `MarketStrip.tsx`: 지수 카드·종목 상승/하락 카드
  - `CompanyPage.tsx` (새, 라우트 `/companies/:id`): 머리·차트·지표·관계 기업·뉴스 이력
  - `ReturnChart.tsx` (새): 종목 vs 지수 누적 수익률 두 선, 마우스 표시. `features/invest/PriceChart.tsx` 의 그리는 방식을 따른다
  - `quoteFormat.ts` (새): 등락률·가격·시가총액(조·억) 표시, 중앙값, 관계 종류 문구, 정렬 비교 — 단위 테스트 대상
- 미니 차트는 SVG 한 줄짜리 컴포넌트(`Sparkline`)로 `components/` 에 둔다. 색은 지금 화면과 같게 오르면 `--pos`(초록), 내리면 `--neg`(빨강)
- 차트 라이브러리는 들이지 않는다(지금처럼 SVG 직접)
- 쿼리: `useMarketIndices(period)`, `useCompanyQuotes()`, `useRelatedCompanies(id)` 를 `lib/queries.ts` 에
- 미리보기 창(`CompanyPreviewDialog`)은 다른 곳에서 쓰면 남기고, 목록에서는 쓰지 않는다

## 5. 오류·빈 값

- 주가가 없는 기업: 시세 칸 '-', 상세 차트 자리에 "주가 데이터가 없습니다"
- 재무 일부만 있음: 있는 칸만 채운다(`docs/api-specification.md` 의 fundamental 설명과 같다)
- 관계 기업이 없음: "공개 은하 스냅샷에 이 기업의 1년 관계가 없습니다"
- 지수 API 실패: 시장 띠만 숨기고 표는 그린다

## 6. 테스트

- 백엔드 통합 테스트(기존 `EventServiceIntegrationTest` 처럼 테스트 DB에 행을 넣고 부른다)
  - quotes: 1일·1개월·3개월 등락률, spark 21개 순서, 주가·재무 없는 기업의 null, 1Y 관계 수
  - related: 방향별 관계 종류, 같은 상대 묶기, 점수 순서, 404
  - indices: 기간별 점수 개수, 1일·1개월 등락
- 프론트 단위 테스트(`tests/*.test.ts`): `quoteFormat` 의 표시·중앙값·관계 문구·정렬
- 브라우저: 목록 정렬·필터, 상세 차트 기간 전환과 마우스 표시, 관계 기업 클릭 이동, 좁은 화면

## 7. 문서

- `docs/api-specification.md` 에 새 API 3개
- `docs/nexus/existing-vs-new.md` 에 본선 신규로 추가(기업 목록 화면은 COSMOS 기존, 시세판·상세 페이지·API 3개는 신규)

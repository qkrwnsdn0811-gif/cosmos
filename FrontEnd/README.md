# COSMOS Web

뉴스·공시에서 추출한 기업 간 관계를 3D 은하로 탐색하는 프론트엔드입니다.
React 19 + Vite + TypeScript + React Three Fiber 로 작성했으며, `docs/api-specification.md` 의 계약을 그대로 타입으로 옮겨 사용합니다.

```bash
npm install
npm run dev        # http://localhost:5173  (기본: 브라우저 내 목업 데이터)
npm run build      # tsc 타입 검사 + vite 빌드 → dist/
npm run typecheck
```

## 화면

| 경로 | 내용 |
| --- | --- |
| `/` | 전체 은하. 연관도가 높은 기업일수록 가까이 놓이는 나선 은하 원반(허브가 핵, 팔을 따라 별가루)과 관계 간선. 노드 클릭 → 워프 → 기업 중심 관계망(depth 궤도) + 우측 인텔리전스 패널(뉴스·주가·게시판). 관계선 클릭 → 관계 상세·근거 뉴스 패널. `/` 키로 기업 검색, `?company={id}` 로 진입 가능. 로그인하면 상단 토글로 ‘내 은하’(`?scope=mine`)로 전환 — 관심 기업과 그 1홉 관계만 같은 나선 배치로 보여 주는 개인 은하. 이웃 포함 여부 토글, 기업 추가(검색), 스냅샷 밖 관심 기업 표시 |
| `/news` | 좌측 뉴스 레일 + 우측 관계 지도(선택 뉴스 관련 기업 ±1홉) + 영향도 랭킹·최강 전이 경로·근거 문장. 노드 클릭 시 좌측이 그 기업 뉴스로 전환 |
| `/companies` | 관심 기업, 기업 디렉터리(검색·시장·산업), 투자·지분 관계 트리 |
| `/me` | 내 정보·관심 기업·스크랩 (프로필 메뉴에서 진입) |
| `/lab/art` | 아트 디렉션 랩 (내비게이션 미노출). 항성 1·행성 3종·관계 빔·로고 이름표만 놓고 형태·머티리얼·팔레트를 확정하는 실험 페이지. 팔레트는 `src/features/lab/toon.ts` |

카메라는 은하 중심(또는 선택한 기업)을 도는 궤도 카메라 하나입니다. 드래그 또는 방향키(`← → ↑ ↓`)로 회전, `W A S D` 로 시선 중심 이동, `Shift` 가속, 휠로 확대하며 기업을 클릭하면 워프합니다.

관계를 읽는 도구: 관계선을 호버하면 유형·방향·점수·근거 수 툴팁이 뜨고, 기업을 호버하면 1홉 이웃에 관계 유형 색 링(방향 쐐기 포함)과 이름표 역할 태그(공급사·고객·투자자·피투자·협력·경쟁)가 붙습니다. 기업 중심 뷰에는 1홉 요약 스트립이 있어 역할별로 걸러 볼 수 있고, `Shift+클릭`으로 기준 기업을 고정하면 다른 기업 호버 시 최단 경로가 상단에 표시됩니다. 기업을 누르면 관련 행성이 그 기업을 중심으로 빨려 들어가 궤도에 안착합니다.

간선은 표시 점수(0~100)가 높을수록 굵고 밝고 빠르게 흐르며, 색은 `impactDirection`(긍정 파랑 · 부정 빨강 · 중립 보라)을 나타냅니다. 관계 유형은 색이 아니라 펄스 방향(방향 관계)과 점선(무방향 관계)으로 구분합니다. 방향이 있는 관계는 source → target 으로 흐르고, 무방향 관계는 양 끝에서 흐릅니다.

### 뉴스·공시 비율 (표시 점수)

서버는 시스템 기본 비율(뉴스 50 · 공시 50)로 계산한 공통 점수 `score` 와 구성 점수 `newsScore` · `disclosureScore` 를 함께 내려줍니다. 화면은 관계 필터 카드(우하단)의 슬라이더로 이 비율을 바꾸고, 표시 점수를 프론트에서만 다시 계산합니다 (`src/lib/score.ts`).

- 구성 점수가 둘 다 있으면 `newsScore × newsWeight + disclosureScore × (1 - newsWeight)`
- 한쪽이 `null` 이면 가중치와 무관하게 있는 쪽 점수를 그대로 씁니다
- 둘 다 `null` 이면 그 관계는 화면에 올리지 않습니다 (간선 목록에서 제외)
- 서버가 아직 구성 점수를 주지 않으면 공통 점수 `score` 를 그대로 씁니다

가중치는 서버에 저장하지 않습니다. `sessionStorage`(`cosmos.weight.news`)에 뉴스 가중치만 두어 같은 탭의 페이지 이동·새로고침에는 유지되고, 탭 세션이 끝난 뒤 다시 들어오면 기본 50 : 50 으로 돌아갑니다 (`src/store/weight.ts`). 비율을 바꿔도 그래프를 다시 요청하지 않고 받은 데이터를 다시 섞기만 합니다.

## 데이터 모드

| `VITE_API_MODE` | 동작 |
| --- | --- |
| `mock` (기본) | `src/mock/` 이 명세 형태의 데이터를 브라우저에서 생성합니다. 190여 개 기업(KOSPI·NASDAQ 중심), 큐레이션된 관계 300여 개, 뉴스·주가·댓글. 로그인/관심/스크랩/댓글은 localStorage 에 저장됩니다. 체험 계정 `demo@cosmos.dev` / `cosmos123` |
| `live` | `/api` 를 실제 백엔드로 호출합니다. `vite.config.ts` 의 프록시(`VITE_API_PROXY`, 기본 `http://localhost:8080`)를 통해 Spring API 로 전달됩니다 |

두 구현은 `src/api/contract.ts` 의 `CosmosApi` 인터페이스를 공유하므로, 화면 코드는 모드와 무관합니다.

## 구조

```
src/
  api/        타입(types.ts) · 계약(contract.ts) · fetch 클라이언트(client.ts, Bearer + 401 TOKEN_EXPIRED 시 refresh 1회) · live 구현
  mock/       목업 데이터 시드(data.ts) · 우주 빌더(universe.ts) · 목업 API(mockApi.ts)
  lib/        레이아웃(graph.ts) · 팔레트·관계 메타(meta.ts) · 포맷 · react-query 훅(queries.ts)
  store/      zustand — session(인증), galaxy(뷰·필터·패널), ui(다이얼로그·토스트)
  three/      R3F 씬 — Scene(카메라 연출·워프·후처리), CompanyNodes(인스턴스 항성), RelationLines(리본 셰이더 간선), Labels, Starfield, Nebula, Warp, MiniScene
  features/   galaxy · news · companies · profile · auth
  components/ 헤더, 공통 UI, 주가 차트
```

## 기업 로고

`public/company-logos/` 의 로고는 `npm run logos:fetch` 로 수집합니다 (`scripts/fetch-logos.mjs`). 종목코드·티커별 대표 도메인(국내 계열사는 그룹 도메인 병행)을 Clearbit → 사이트 apple-touch-icon → Google favicon → DuckDuckGo 아이콘 → 사이트 favicon → logo.dev 순으로 시도해 저장하고, 결과 목록을 `src/lib/logo-codes.json` 에 씁니다. 파일이 없는 기업은 3D 노드와 아바타에 모노그램이 표시됩니다. 수집된 로고는 각 기업의 상표이며, logo.dev 폴백은 공개 문서용 키를 쓰므로 실제 서비스에서는 자체 키와 출처 표기가 필요합니다.

## 백엔드에 확인이 필요한 항목 (프론트의 가정)

`docs/api-specification.md`·`docs/database-design.md`·`V1__create_initial_schema.sql` 을 기준으로 맞췄고, 그래도 남는 빈칸은 아래처럼 가정했습니다. 목업(`VITE_API_MODE=mock`)은 가정대로 값을 채워 화면을 보여 주고, live 모드는 값이 없으면 그 자리를 접습니다.

**기업 기준 정보**

- **시가총액·상장주식수**: `company` 테이블에도 기업 상세 응답에도 없습니다. `CompanyDetail.marketCap` · `listedShares` 를 선택 필드로만 열어 두었고, 주가 탭의 '시가총액' 칸은 값이 올 때만 나타납니다(목업은 `최근 종가 × 상장주식수` 로 채웁니다). ① `company` 에 컬럼을 추가할지 ② 별도 시계열로 둘지 ③ 상세 응답에 실을지 확인이 필요합니다. 값이 없는 동안은 '평균 거래대금(종가×거래량)' 으로 대신합니다.
- **`graphs/latest` 노드에 원화 환산 시가총액(`marketCapKrw`) 필요**: 은하 뷰의 행성 크기가 연결 가중치에 시가총액을 더해 정해집니다(로그 스케일 5~95 백분위 순위). 시장 통화가 달라 프론트가 환산할 수 없으므로 서버가 원화(또는 한 통화)로 환산한 값을 노드에 실어 주어야 합니다. 지금은 `LatestGraphNode.marketCapKrw` 를 선택 필드로 열어 두고 목업이 대략적인 환율로 채우며, 값이 없는 기업은 연결 가중치만으로 크기를 정합니다(그래도 예전보다 약 10% 큽니다).
- **시장 코드와 통화**: 가격 표기 통화를 `company.market` 에서 파생합니다(KOSPI·KOSDAQ→₩, NASDAQ·NYSE·AMEX→$, TSE→¥, SSE·SZSE→CN¥ / `src/lib/meta.ts` `marketCurrency`). 목록에 없는 코드는 원화로 폴백하니, 수집 대상 시장 코드 목록이 확정되면 알려 주세요.
- **업종코드·대표자·설립일·홈페이지**: DDL 에 컬럼이 없어 산업 분류는 `industry`/`company_industry` 만으로 표현한다고 가정합니다. 표준 업종코드(KSIC·GICS)·`ceo_name`·`founded_on`·`homepage_url` 을 추가할 계획이 있는지 확인이 필요합니다.
- **`industry.name` 실제 목록**: V1 에 시드가 없어 목업 18종(`src/mock/data.ts`)을 씁니다. 산업 팔레트(`src/lib/meta.ts` `INDUSTRY_COLORS`)가 이 이름에 맞춰져 있어, 실제 산업명이 정해지면 두 곳을 같이 맞춰야 합니다.
- **`company.status`(ACTIVE·상장폐지 등)** 를 목록·상세 응답에 포함할지. 오면 목록 표에서 비활성 기업을 흐리게 처리할 계획입니다.
- **`company_alias`**: `alias_type` 값 목록과 실제 별칭 데이터가 없어 목업 별칭은 프론트가 임의로 큐레이션했습니다(검색 결과 폭이 실제와 달라질 수 있습니다). 실 백엔드는 `keyword` 하나만 받으므로 프론트 변경은 없습니다.

**공시**

- 명세 서비스 범위가 "공시는 관계 분석에 사용하지만 화면에 문서 자체를 공개하지 않는다" 이고 조회 엔드포인트도 없어, **공시 목록·상세·뉴스/공시 구분 배지는 구현하지 않았습니다.** 공시를 노출할 계획이 있다면 목록·상세 엔드포인트와 공개 범위(`report_name`·`filing_date`·`disclosure_type`·`correction_status`·DART 링크)를 알려 주세요. 확정되면 `NewsItem.documentType` 추가 + 뉴스 목록 필터 칩에 '공시' 를 더하는 것이 최소 경로입니다(지금 넣으면 항상 `NEWS` 인 죽은 필드가 됩니다).

**관계·그래프**

- **`relationship_type.code` 목록**: 시드가 없어 `SUPPLY` `INVEST` `PARTNER` `COMPETE` 를 가정하며, 그 외 코드는 회색 폴백으로 표시됩니다(`src/lib/meta.ts`). 특히 **지분 관계 코드가 `INVEST` 가 맞는지** 확인이 필요합니다 — 기업 화면의 '투자·지분 관계' 트리가 `OWNERSHIP_TYPES` 상수 한 곳만 보고 동작하므로, 코드가 다르면 그 배열만 고치면 됩니다. 응답에 `relationshipTypeName`(한글 라벨)이 실리면 `relationshipMeta(code, name)` 로 서버 라벨을 그대로 쓸 수 있습니다.
- **관계 점수 범위**: `relationship_score_current.score` 에 CHECK 가 없어 0~100 스케일을 가정합니다(ScoreBar·'100점 기준' 표기). 상한이 다르면 표기를 바꿔야 합니다. `window_type` 도 `7D`·`30D`·`90D` 3종으로 가정합니다.
- **관계 점수 이력**: `relationship_score_history` 는 있지만 조회 엔드포인트가 없습니다. 열어 주시면 관계 패널의 '관계 점수' 아래에 추이 차트를 붙일 수 있습니다. 그래프 응답에 `formulaVersion`·`modelVersion` 을 포함할지도 확인이 필요합니다.
- **`graphs/latest` 노드에 `priceChange`(1D·1M·3M) 포함 또는 일괄 조회 엔드포인트 필요**: 은하 뷰가 기업의 기간 등락률로 행성 높이를 정합니다(위 = 상승, 아래 = 하락). 지금은 `LatestGraphNode.priceChange` 를 선택 필드로 열어 두고 목업이 주가 시계열에서 채우며, 값이 없는 기업은 원반 평면에 남습니다. 노드 수가 수백이라 기업별 `stock-prices` 를 개별 호출할 수는 없으니 ① 그래프 응답에 세 기간 등락률을 실어 주시거나 ② 여러 기업의 등락률을 한 번에 주는 엔드포인트가 필요합니다.
- **기업 중심 그래프**(`/api/graphs/companies/{id}`)의 노드에 `industryName`·`market` 이 없어 전체 그래프를 한 번 받아 클라이언트에서 조인합니다. 응답에 두 필드를 추가하면 조인이 불필요합니다.

**사용자·공통**

- **개인 은하는 클라이언트에서 파생**합니다: `graphs/latest`(전체 우주) ∩ `users/me/watch-companies` + 1홉. 우주(최대 200개) 밖의 관심 기업은 배치할 수 없어 ‘스냅샷 밖’으로만 표시합니다. `graphs/latest` 가 `companyIds`(또는 `watch=true`) 파라미터로 관심 기업 중심 부분 그래프를 서버에서 내려 줄 수 있는지 확인이 필요합니다.
- **인증 API 구현 상태**: 백엔드에는 현재 `signup`·`check-email`·`check-nickname` 만 있고 `login`·`refresh`·`logout`·`users/me` 는 명세만 있습니다. 프론트는 가입 후 자동 로그인을 시도하고 실패하면 로그인 폼으로 안내하며, 게스트는 첫 로드에서 `refresh` 를 호출하지 않습니다(세션 힌트 플래그). refresh 토큰은 HttpOnly 쿠키 `refresh_token` 이라고 가정하고 모든 요청을 `credentials: include` 로 보냅니다.
- **`userId` 는 64비트 정수**(`users.user_id BIGSERIAL`)로 맞췄습니다. `companyId`·`newsId`·`relationshipId`·`commentId`·`industryId`·`snapshotId` 는 UUID 문자열 그대로라고 가정합니다.
- **`users/me` 에 `role`** 을 포함할지. 관리자 화면이 MVP 밖이라 지금은 타입에 넣지 않았습니다.
- **댓글 `edited`**: DDL 에 컬럼이 없어 백엔드가 `created_at ≠ updated_at` 으로 파생한다고 가정합니다. 내용 길이는 명세대로 1~500자로 맞췄습니다(입력창에 카운터 표시).
- **선택 필드는 `null`**: DDL 에서 NULL 을 허용하는 필드(`nameEn`·`stockCode`·`description`·`summary`·`publisher`·`publishedAt`·`sentimentScore`·`confidence`·`impactDirection`·`contributionScore`·`relevanceScore` 등)를 모두 nullable 로 선언하고, 화면은 `-` 로 비우거나 구분점째 접습니다.
- **날짜 표시 시간대**: `database-design.md` 규칙대로 UTC 를 **브라우저 시간대**로 변환해 표시합니다(기존 KST 고정 해제). 심사·데모를 항상 KST 로 보여 줘야 한다면 이 가정이 뒤집힙니다.
- **`stock_price.trading_at` 의 저장 규칙**: 거래일은 '몇 시'가 아니라 '어느 거래일'이라 시간대 변환 없이 **UTC 기준 날짜**로 읽습니다(`fmtTradingDate`). 시장 현지 종가 시각(KRX 06:30Z·NYSE 20:00Z)으로 저장하든 날짜 전용 UTC 자정으로 저장하든 날짜가 밀리지 않지만, **시장 현지 자정**(예: `2026-09-10T00:00+09:00`)으로 저장하면 하루 밀립니다. 어느 쪽인지 알려 주세요.
- 화면구성도의 게시판(글 제목·카테고리·댓글)은 명세의 단일 계층 댓글 API 로 축소해 구현했습니다.

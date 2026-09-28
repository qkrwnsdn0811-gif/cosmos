# COSMOS 프론트엔드 — 백엔드 연동 준비 체크리스트

`docs/api-specification.md`, `docs/database-design.md`, `docs/CICD.md`, `docs/infrastructure-design.md`, `docs/HADOOP_CLUSTER.md`, `FrontEnd/README.md`, `FrontEnd/src/api/*`, `FrontEnd/src/lib/*`, `FrontEnd/src/features/galaxy/*`, `BackEnd/src/main/java/com/cosmos/api` 패키지 구조를 확인하고 정리했다. 프론트는 `VITE_API_MODE` 하나로 mock↔live를 전환하도록 설계돼 있어(`src/api/index.ts`), 연동 자체보다는 **백엔드가 아직 채우지 못한 부분에서 화면이 어떻게 보일지**를 준비하는 작업이 중심이다.

## 0. 백엔드 API 구현 현황 (연동 범위 판단 기준)

`BackEnd/src/main/java/com/cosmos/api` 패키지 기준.

| 영역 | 상태 | 비고 |
| --- | --- | --- |
| `auth`(회원가입·로그인·로그아웃·refresh·이메일/닉네임 중복확인) | 구현됨 | JWT 필터·Refresh 쿠키까지 포함 |
| `companies`(목록·상세·지표·지표이력·주가·산업) | 구현됨 | |
| `graph`(latest·기업중심·관계상세·근거) | 구현됨 | 단, 운영 DB에 `PUBLISHED` 스냅샷 적재 여부는 별도 확인 필요(1.1) |
| `news`(목록·상세) | 구현됨 | |
| `users/me` 조회·닉네임 수정 | 구현됨 | `UserController` |
| `users/me/scraps`, `watch-companies`, `weight-setting` | **미구현** | 엔티티·컨트롤러 없음 |
| `community`(댓글) | 구현됨 | `CommunityCommentController` — 전체/기업별 조회, 작성, 수정, 삭제 |

→ 은하·뉴스·기업 디렉터리·투자지분 관계·댓글까지는 API가 갖춰져 있고, **관심기업(워치)·스크랩·가중치는 여전히 백엔드 작업이 남아** 있어 그 부분만 실패 처리 또는 비활성화가 필요하다.

## 1. 공통 준비 (모든 화면에 영향)

### 1.1 그래프 스냅샷 적재 여부 — 가장 먼저 확인할 항목

`docs/CICD.md`에 "새 운영 DB에는 공개된 그래프 스냅샷이 아직 없다"고 기록돼 있고, `docs/HADOOP_CLUSTER.md`는 데이터 파이프라인용 Hadoop/Spark 클러스터(Worker 3대) 인프라 구축·검증까지만 다루고 있어 이 문서만으로는 실제 `GRAPH_SNAPSHOT` 적재 여부를 확인할 수 없다.

- [ ] `/api/graphs/latest`가 실제로 `200`을 내려주는지, 아니면 여전히 `404 GRAPH_SNAPSHOT_NOT_FOUND`인지 확인
- [ ] 스냅샷이 없는 상태에서 은하·기업중심뷰·뉴스 관계지도의 "로딩 실패" UI가 사용자에게 말이 되게 뜨는지 확인(mock은 항상 스냅샷이 있어 이 경로가 자동으로 검증되지 않음)

### 1.2 환경변수 · 로컬 기동

- [ ] `FrontEnd/.env`: `VITE_API_MODE=live`, `VITE_API_BASE=/api`, `VITE_API_PROXY=http://localhost:8080`
- [ ] `BackEnd/.env`: `JWT_SECRET` 채우기(32자 이상, 없으면 서버 기동 자체가 안 됨)
- [ ] `docker-compose`로 PostgreSQL·Redis 기동 — Redis는 인증(Refresh Token 저장)에 필수
- [ ] `./gradlew bootRun` + `npm run dev`(live 모드) 조합으로 로컬 연동 확인

### 1.3 CORS · 쿠키 · 인증 흐름

- [ ] `credentials: "include"`로 요청 — 백엔드 `app.cors.allowed-origins`에 프론트 origin 포함 확인(로컬 기본값은 이미 맞음)
- [ ] Access Token 메모리 보관 + 만료 60초 전 자동 refresh, `401 TOKEN_EXPIRED` 시 1회 재시도, `401 TOKEN_INVALID` 시 게스트로 전환 — 실 로그인으로 각 분기 동작 확인
- [ ] Refresh Token 쿠키명이 `refresh_token`인지 확인
- [ ] 배포 환경에서 프론트·API 도메인이 다르면 `COOKIE_SAME_SITE=None`(+`Secure=true`) 필요, 로컬은 기본값(`Lax`/`false`)으로 충분
- [ ] `users/me` 조회·닉네임 수정이 구현돼 있으므로, 로그인 직후 닉네임을 포함한 사용자 정보 조회가 실제로 정상 동작하는지 확인

## 2. 화면별 준비 사항

### 2.1 전체 은하 (`/`) — 항목이 가장 많음

은하 뷰는 `LatestGraphNode`의 선택 필드 두 개(옵셔널)에 강하게 의존한다. 코드는 값이 없을 때 접히도록 방어돼 있지만, 실제로 없을 때 화면이 허전해 보이지 않는지는 실 데이터로 봐야 한다.

- [ ] `marketCapKrw`(원화 환산 시가총액): 행성 크기 연출에 사용. 값이 없으면 연결 가중치만으로 크기를 정함 — 백엔드가 이 필드를 줄 계획·시점 확인
- [ ] `priceChange`(1D/1M/3M 등락률): 행성 높이(위=상승/아래=하락) 연출에 사용. 값이 없는 기업은 평면에 남음 — 그래프 응답에 실어줄지, 별도 일괄 조회 엔드포인트가 필요한지 결정 필요
- [ ] 노드 200개 규모 기준 `graphs/latest` 실제 응답 시간 확인
- [ ] `industryName`, `market` 필드가 응답에 채워져 있는지 — 없으면 산업 필터·색상 링이 깨짐
- [ ] 관계선 색상(`impactDirection`)·굵기(`score`)가 0~100 스케일이 맞는지, `null` 케이스 유무 확인
- [ ] 첫 방문 투어(`Tour.tsx`)·도움말 허브(`HelpHub.tsx`)·"읽는 법" 바(`ReadBar.tsx`)·카메라 프리셋·"뜻밖의 관계" 추천(`SurpriseRail.tsx`)은 모두 이미 받아온 그래프 스냅샷 필드(유형·점수·산업·시장·연결 수·배치 좌표)만으로 클라이언트에서 계산·렌더링되므로, 이 기능들 자체는 추가 API가 필요 없음
- [ ] 첫 방문 투어 완료 여부는 현재 기기별 `localStorage`(`cosmos_tour_done`)로만 저장됨. 로그인 사용자의 투어 완료 상태를 서버에 저장하는 기능은 아직 백엔드 요청 목록에만 있는 상태 — 구현 시점과 엔드포인트 형태를 백엔드와 맞춰야 함
- [ ] **개인 은하(`?scope=mine`)**: `users/me/watch-companies`가 백엔드에 없어 이 기능은 이번 연동 범위에서 동작하지 않음 — 로그인 사용자에게 보이는 "내 은하" 토글을 숨기거나 "준비 중" 안내로 처리할지 결정 필요

### 2.2 뉴스 (`/news`)

- [ ] `list`/`detail` API 필드(`relatedCompanies`, `sentiment`, `evidence`)가 실 데이터에서 명세 형태 그대로 오는지 확인
- [ ] 스크랩 등록/해제(`users/me/scraps`)는 백엔드 미구현 — 로그인 사용자에게도 스크랩 버튼이 실패하므로 비활성화 또는 안내 문구로 대체할지 결정 필요
- [ ] 뉴스 관계 지도(선택 뉴스 ±1홉)는 뉴스 상세의 `relatedCompanies`로 클라이언트에서 구성 — 그래프 스냅샷 부재와 무관하게 동작해야 하지만, 언급 기업이 최신 그래프 200개 유니버스 밖이면 관계 지도가 빈약해질 수 있어 실 데이터로 확인 필요

### 2.3 기업 (`/companies`)

- [ ] 디렉터리(검색·시장·산업 필터, 정렬) 그대로 연동
- [ ] **투자·지분 관계 트리**: `OWNERSHIP_TYPES = ["INVEST"]` 상수 하나로 동작 — `relationship_type.code`가 실제로 `INVEST`가 맞는지 확인(다르면 `src/lib/meta.ts` 상수만 수정)
- [ ] 관심 기업(워치) 등록/해제 — `users/me/watch-companies` 미구현이라 실패함. 은하 페이지와 동일하게 비활성화/안내 처리 필요
- [ ] 기업 상세 커뮤니티(댓글) 탭 — API는 구현돼 있음. 프론트가 기대하는 응답 형태(전체 댓글은 `company` 참조 포함, 기업별 댓글은 `companyId`만 포함하는 서로 다른 모양)가 실제로 그대로 오는지, 내용 1~500자 검증·수정/삭제 권한(작성자 본인만) 처리가 명세대로인지 확인
- [ ] 기업 중심 그래프(`/api/graphs/companies/{id}`) 응답에 `industryName`·`market`이 포함되는지 — 없으면 현재처럼 전체 그래프를 한 번 더 받아 클라이언트에서 조인해야 함

### 2.4 내 정보 (`/me`)

- [ ] `users/me` 조회·닉네임 수정은 붙일 수 있음
- [ ] 관심기업·스크랩·가중치 설정 API는 미구현 — 해당 섹션만 "준비 중" 처리하거나 페이지 진입은 유지하되 개별 카드만 비활성화할지 결정 필요

## 3. 값만 맞으면 되는 것 vs 코드를 고쳐야 하는 것

| 구분 | 항목 | 처리 |
| --- | --- | --- |
| 값만 채워지면 자동 반영 (코드 변경 불필요) | `marketCapKrw`, `priceChange`, `company.marketCap`/`listedShares` | 전부 옵셔널 필드로 이미 열어둠 |
| 상수 파일만 고치면 됨 | `relationship_type.code`(특히 `INVEST`), `industry.name` 목록(→ `INDUSTRY_COLORS`) | `src/lib/meta.ts` 한 곳 |
| API 구현됨 — 응답 형태만 검증 | 댓글 조회·작성·수정·삭제, `users/me` 조회·닉네임 수정 | 명세와 실제 응답 필드 비교 |
| 화면 로직을 새로 붙여야 함 | 스크랩/워치 버튼 활성화, 개인 은하, `/me`의 관심기업·스크랩·가중치 카드, 투어 완료 서버 저장 | 대응 백엔드 API 완성 후 |
| 화면 로직을 임시로 꺼야 함 | 위와 동일한 항목들 | 백엔드 완성 전까지 |

## 4. 검증 순서 제안

1. mock 모드에서 tsc/eslint/build 기준선 유지 확인
2. `.env`를 live로 바꾸고 로컬 백엔드 기동 → 회원가입·로그인·로그아웃·refresh·`users/me` 조회/닉네임 수정 확인
3. companies·news 탭을 실 데이터로 확인
4. 기업 상세 댓글 탭(전체·기업별 조회, 작성·수정·삭제)을 실 데이터로 확인 — 응답 형태 불일치만 잡으면 됨
5. 그래프 스냅샷 적재 여부를 확인하고, 없는 상태라면 은하·기업중심뷰·뉴스 관계지도의 실패 UI 확인
6. 스냅샷이 있는 경우 `marketCapKrw`/`priceChange` 유무에 따른 은하 연출 재확인, 첫 방문 투어·도움말 허브·뜻밖의 관계 추천이 실 데이터에서도 자연스러운지 확인
7. 워치·스크랩·가중치 버튼과 개인 은하 토글이 실패 시 사용자에게 이상하게 보이지 않는지(에러 토스트, 비활성화 등) 확인
8. 백엔드가 워치·스크랩·가중치 API를 붙이면 해당 기능만 다시 검증

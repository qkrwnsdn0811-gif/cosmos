# NEXUS — 뉴스가 어떤 경로로 어떤 종목에 닿는지

2026 뉴스빅데이터 해커톤 본선(한국언론진흥재단) 작품. 빅카인즈 뉴스 한 건이 기사에 나온 기업과, 공급망·경쟁·지분 관계로 이어진 기업에
어떻게 닿는지 보여 주고, 과거 같은 유형 뉴스 뒤 실제 주가 반응과 종목의 변동폭을 함께 보여 준다. 오를지·내릴지는 예측하지 않는다.

- 바탕 코드: SSAFY 프로젝트 **COSMOS**(기업 관계 3D 은하). 본선 작업은 브랜치 `nexus`(원격 `NEXUS`, 2026-10-26에 원격 `main`에 합치고 `NEXUS`는 지웠다), 기준점은 태그 `pre-hackathon-baseline`.
- 무엇이 원래 있었고 무엇을 새로 만들었는지: [existing-vs-new.md](existing-vs-new.md) (대회 규정용 구분표).
- 범위: 국내 상장 반도체 61종목(코스피·코스닥), 2025-01 ~ 최신 빅카인즈 기사.

## 화면

| 메뉴 | 주소 | 보여 주는 것 |
|---|---|---|
| 관계 은하 | `/galaxy` | 61개 기업의 관계 3D 은하. 관계선은 빅카인즈 기사 문장(공급·협력·경쟁·함께 언급)과 OpenDART 공시 지분 |
| 이벤트 (첫 화면) | `/` | 뉴스 피드 → 고른 뉴스의 3D 관계 지도 → 영향 종목 카드(왜 이 종목?, 과거 경향, 예상 변동폭, 뉴스 전후 주가 차트, 근거 문장) |
| 뉴스 | `/news` | 기업이 나온 기사 목록, 상대 논조(긍정·중립·부정), 근거 문장 |
| 기업 | `/companies`, `/companies/:id` | 반도체 시세판(지수 띠, 금색 주목 종목, 정렬되는 시세 표, 투자·지분 관계)과 종목 상세(지수 비교 차트, 투자 지표, 업종 비교, 관계 기업 동향) |
| 투자 성향 맞춤 | 오른쪽 아래 버튼 (은하 화면 제외) | 질문 5개로 간이 성향(안정형~공격투자형)·기간 → 맞는 반도체 종목 최대 6곳과 이유 칩, 관심 등록. 단기는 검증 통과한 변동폭 등급, 장기는 지표로 고른다. 추천·매수 권유 문구는 없다 |

시연 순서와 화면별 숫자: [demo-scenario.md](demo-scenario.md).

## 데이터 흐름

```
빅카인즈 API ──> nexus (Python) ──> PostgreSQL ──> Spring Boot API ──> React 화면
FinanceDataReader(주가·지수) ┘   │
OpenDART(재무·지분) ─────────────┘
```

`nexus` 명령(저장소 루트에서 `nexus/.venv/Scripts/python -I -X utf8 -m nexus …`, 자세한 순서는 [refresh-runbook.md](refresh-runbook.md)):

| 명령 | 하는 일 |
|---|---|
| `collect` | 빅카인즈 기사 수집 (본문은 200자까지만 온다 — [bigkinds-api-findings.md](bigkinds-api-findings.md)) |
| `analyze` | 기업 인식, 관계 추출(규칙 + KPF-BERT 판별기), 이벤트, 공급망 전파 |
| `sentiment` | KR-FinBERT 논조 (GPU 환경 `nexus/.venv-rocm`) |
| `load` | 이벤트·영향·초과수익률·주가·근거 문장 적재 |
| `cases` | 뉴스 유형 15개, 과거 사례 반응표, 방향 표시와 적중률, 예상 변동폭, 피드 순서 |
| `fundamentals` | 시가총액·규모(KRX), PER·PBR·배당(OpenDART) |
| `ownership` | 61개사 사이 지분(OpenDART 타법인 출자 현황) |
| `publish-graph` | 관계 은하 스냅샷 게시 |

## API (본선에서 만든 것)

| 경로 | 내용 |
|---|---|
| `GET /api/events`, `/api/events/{id}` | 뉴스 피드, 뉴스별 관계 지도와 영향 종목 |
| `GET /api/events/{id}/companies/{companyId}/chart` | 뉴스 전후 종목·지수 누적 수익률 |
| `GET /api/events/backtest`, `/api/events/company-history` | 적중률·변동폭 검증, 종목별 뉴스 이력 |
| `GET /api/market/indices` | 코스피·코스닥 지수 |
| `GET /api/companies/quotes`, `/api/companies/{id}/related`, `/api/companies/{id}/fundamental` | 전 종목 시세, 관계 기업 시세, 기업 지표 |
| `GET /api/relationships/{id}` 의 `ownership` | 지분 관계의 공시 지분율·DART 원문 링크 (COSMOS API 에 칸 추가) |

요청·응답 예시: [../api-specification.md](../api-specification.md).

## 검증한 것과 한계

- 관계 판별기: 시험 정밀도가 기준(80%, `APPLY_PRECISION`)을 넘을 때만 서비스에 적용하고, 못 넘으면 이전 모델을 그대로 쓴다 — [relation-model-report.md](relation-model-report.md)(마지막 학습 결과).
- 과거 사례·적중률: 통계는 2026-06까지 뉴스로만 만들고 7월부터 확인. 방향 적중률은 기준선보다 낮아 화면에 '예측'으로 쓰지 않는다 — [case-reaction-report.md](case-reaction-report.md).
- 예상 변동폭: 단기(3거래일) 등급은 시험 기간에 순서대로 맞았고 장기는 아니라서 단기에만 등급을 붙인다. 사실상 종목의 평소 변동폭이다.
- 다음 날 방향 예측 모델은 '항상 변동 없음'과 같은 수준이라 쓰지 않았다 — [impact-model-report.md](impact-model-report.md).

## 실행과 배포

- 개발 PC: `docker start cosmos-postgres`(+ Redis), 백엔드 `SERVER_PORT=18081`(+ `JWT_SECRET`), 프런트 `npm --prefix FrontEnd run dev`.
- 테스트: `nexus/.venv/Scripts/python -I -X utf8 -m pytest nexus/tests -q`, `cd BackEnd && ./gradlew test`(테스트 DB `cosmos_test`), `cd FrontEnd && npm test && npm run typecheck`.
- 배포 서버 갱신: [deploy-update.md](deploy-update.md) (이미지 빌드 → DB 덤프 복원 → 배포, 회원가입용 Gmail 앱 비밀번호).

## 설계와 계획 문서

`docs/superpowers/specs/`(설계)와 `docs/superpowers/plans/`(구현 계획)에 날짜순으로 있다: 빅카인즈 설계(10-13), 영향 모델(10-16),
빅카인즈 은하(10-18), KPF-BERT 관계 판별기(10-18), 투자자 화면(10-25), 기업 탭 주식 화면(10-26). 첫 실행 기록은 [slice1-run.md](slice1-run.md).

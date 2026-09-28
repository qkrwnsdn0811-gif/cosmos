# 관계 점수 개인화 — AI·Loader 계약

`rel-v0.3-components`는 **기업 관계의 근거 지지 점수**를 뉴스와 공시로 나눕니다.
GNN의 기사별 `impact_score`와 다르며, 모델 재학습이나 사용자 가중치 저장은 없습니다.
기존 `rel-v0.2-typed`와 그 CSV는 과거 실험 재현용으로 유지합니다.
기존 전체 기간 점수를 7D·30D·90D에 복사하지 않습니다.

## 계산 기준

- `as_of`는 배치 마감 시각. 각 창은 UTC `[as_of - N일, as_of)`입니다.
- `--as-of 2026-09-15`는 9월 15일 00:00 UTC이며, 날짜만 있는 근거는 해당 UTC 날짜의 끝에 가용한 것으로 처리합니다.
- 뉴스는 발행일, 공시는 **접수일**을 사용합니다. 지분의 회계 기준일을 접수일로 대신하지 않습니다.
- 현재 정책은 기간 안에 접수된 공시만 집계하는 것입니다. 오래된 지분 공시의 지속 유효성을 판단하는 정책은 포함하지 않습니다.
- `(기업 쌍, 관계 유형, 출처, 문서 ID)`별 한 건. 여러 문장의 신뢰도는 최댓값을 사용합니다.
- PARTNER·COMPETE는 종목 코드 순으로 정규화합니다. SUPPLY·INVEST 방향은 유지합니다.
- 각 출처의 점수: `100 × (1 - exp(-문서별 신뢰도 합 / 5))`.
- 뉴스 신뢰도는 추출기의 0~1 값, DART 지분 표 근거는 1입니다. 지분율·주가 상관·업종 신호는 섞지 않습니다.
- 신뢰도 1인 독립 문서 5건은 63.212056점입니다. 분모 5는 공개된 초기 표시 규칙이며, 성능 검증으로 선택한 값이 아닙니다. 출처별 실제 분포의 보정은 후속 과제입니다.
- 신뢰도 0인 근거가 존재하면 점수도 0입니다. **근거가 없는 출처만 NULL**입니다.
- 둘 다 존재: `(news_score + disclosure_score) / 2`. 하나만 존재: 그 점수. 모두 없음: 해당 관계·기간 행을 출력하지 않습니다.
- `evidence_count`는 두 출처의 중복 제거 문서 수 합계입니다. `confidence`는 그 문서별 신뢰도 평균입니다.
- 서로 다른 관계 유형의 근거는 합치지 않습니다. 뉴스 PARTNER를 같은 쌍의 공시 INVEST에 복사하지 않습니다.

## 입력과 실행

뉴스의 **전체 관계 근거** JSONL 또는 Spark JSON export 디렉터리가 필요합니다.
로컬 `data/rel_hits.jsonl`은 검수 표본이므로 전체 그래프 발행에 사용하면 안 됩니다.
원격 `spark/build_relation_edges.py` 실행에 `--hits-out <새 경로>`를 추가하면 집계·최소 기사 수 필터 전 근거를 내보냅니다.
기존 필수 인자와 `--py-files extract_relations.py`는 그대로 사용합니다.
HDFS export는 `_SUCCESS`와 `part-*`를 모두 로컬/분석 서버로 가져와 입력합니다.

```json
{"record_id":"news-001","published_date":"2026-09-14","src_ticker":"005930","dst_ticker":"000660","rel_type":"PARTNER","confidence":0.8}
```

공시도 같은 JSONL 스키마로 `--disclosure-evidence`에 전달할 수 있습니다.
`record_id`는 접수번호, `published_date`는 접수일, `rel_type`은 공시에서 확인한 관계입니다.
이 옵션을 생략하면 `--ownership` CSV의 `evidence` 안 `dart:접수번호`로 INVEST 근거를 만듭니다.
**기존 ownership CSV는 최신 보고서만 보유하므로 완전한 기간별 공시 이력은 아닙니다.**
정식 발행은 전체 기간을 덮는 공시 근거 JSONL을 권장합니다. 최신 보고서 CSV로 과거 스냅샷을 복원하지 마세요.
파일 없음/잘못된 값/동일 문서의 발행 시각 불일치는 실패합니다. 수집이 정상 완료됐지만 근거가 없을 때만 빈 파일을 사용합니다.

저장소 루트 PowerShell:

```powershell
$env:PYTHONUTF8='1'
AI/ner/.venv/Scripts/python.exe AI/graph/build_relationship_seed.py --components --news-hits <전체뉴스근거경로> --disclosure-evidence <전체공시근거.jsonl> --as-of 2026-09-15 --out AI/graph/artifacts/relationship_components_20260915
```

기업 사전 밖 종목은 제외하고 `component_summary.json`에 기록합니다. 발행 전에 이 목록과 각 기간 행 수, 입력의 완전성을 확인합니다.
출력은 관계 유형·스냅샷·관계·현재 점수 CSV 네 개, `load_relationships.sql`, 집계 요약입니다.
CSV의 빈 구성 점수 필드는 PostgreSQL `NULL`로 읽습니다. 이전 버전 CSV와 새 Loader를 섞지 않습니다.

## PostgreSQL 적재

BE의 **새 Flyway 마이그레이션**이 먼저 두 점수 테이블에 nullable `NUMERIC`인
`news_score`·`disclosure_score`를 추가해야 합니다. AI Loader는 Flyway나 운영 스키마를 변경하지 않습니다.
기존 `score NOT NULL`이어도 양쪽 근거가 없는 행을 생략하므로 호환됩니다.

출력 디렉터리에서 `psql "$DATABASE_URL" -f load_relationships.sql`로 실행합니다.
현재와 이력을 한 트랜잭션으로 적재하며, 같은 스냅샷 재실행은 UPSERT합니다.
이력의 `period_start/end`는 각각 창 시작/배치 마감이고 종료는 배타적입니다.
근거가 만료된 현재 행은 제거하고 이전 스냅샷 이력은 유지합니다. 같은 스냅샷의 재발행은 그 스냅샷의 이력도 정정합니다.
현재 데이터보다 오래된 배치, 기업 UUID 매핑 누락, 컬럼 누락, 점수 불일치, 중복 자연키는 실패·롤백합니다.
**세 창 전체를 교체하는 완전한 배치용 Loader입니다. 표본이나 부분 기업 데이터는 적재하면 안 됩니다.**

이번 작업은 로컬 계산·CSV·Loader 구현까지이며 원격 추출, PostgreSQL 적재, BE/FE 연결은 실행하지 않습니다.

## 검증 기록

- 구성 점수 테스트 12개 통과: 7/30/90일 경계, 미래 근거 제외, 출처별 중복 제거, 0과 NULL 구분,
  방향 유지, DART 접수일, 불완전 입력 거절, 실제 CLI의 CSV·Loader 생성 및 빈 배치 처리.
- 기존 영향도 회귀 테스트 30개도 통과했습니다.
- 로컬 Docker/PostgreSQL이 실행 중이지 않아 SQL의 실제 DB 적재·롤백·재실행 검증과 원격 Spark 실행은 미실행입니다.
  BE의 새 마이그레이션 적용 후 격리된 테스트 DB에서 먼저 확인해야 합니다.

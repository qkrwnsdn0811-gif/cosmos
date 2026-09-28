# 뉴스·공시 문서 RDB Loader

국내·해외 뉴스와 DART·SEC 공시의 **완료된 HDFS 저장 단위 한 개**를 읽어 PostgreSQL의 문서 테이블에 적재한다. 주가 Loader와 관계 점수 Loader는 별도 모듈이다.

```text
뉴스 Writer → AI 기업 언급 분석 / DART 수집기 / SEC 수집기
  → 완료된 HDFS 분석 배치·스냅샷·공시
  → 문서 메타데이터 검증·기업 식별자 연결
  → source_document + news_article/disclosure + company_document
  → 같은 트랜잭션에서 document_load_batch 기록
```

원문은 HDFS에 보존한다. DB에는 제목, 원문 URL, 출처, 발행·수집 시각, 원문 해시, HDFS 경로와 AI가 완료 게시한 기업 연결을 저장한다. Loader가 요약·감성·관계 점수를 새로 계산하지는 않는다.

## 설치

Python 3.12 환경에서 이 디렉터리를 작업 경로로 사용한다.

```bash
cd Crawling/documents
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m services.document_loader.cli --help
```

Windows에서는 생성한 가상환경의 `Scripts/python.exe`를 사용한다. HDFS를 직접 읽는 서버에는 Hadoop CLI와 해당 클러스터 설정이 필요하다. `--hdfs-bin /opt/hadoop/bin/hdfs` 또는 `HDFS_BIN`으로 실행 파일을 지정한다.

## 먼저 DB 없이 입력 검증

`validate`는 HDFS/로컬 입력과 정규화 결과만 검증하며 DB에 접속하지 않는다. `DOCUMENT_DATABASE_URL`도 필요하지 않다.

```bash
python -m services.document_loader.cli validate \
  --kind news-analyzed \
  --input 'hdfs://namenode.example.internal:9000/data-lake/analyzed/news/company-mentions/model_version=dict-v1.3/topic=news.raw/partition=0/start=00000000000000000000' \
  --hdfs-bin /opt/hadoop/bin/hdfs
```

위 호스트와 경로는 형식 예시다. 실제 게시된 경로로 바꾼다. 결과 JSON의 `records`, `document_types`, `required_companies`, `sources`, `verification`을 확인한다. `validate` 성공은 회사·출처의 DB 등록 여부나 DB 권한까지 검증했다는 뜻은 아니다.

로컬에 보관한 실제 입력을 검증할 때는 원래 HDFS 주소를 `--source-uri`로 보존할 수 있다. 뉴스와 SEC 증분 입력은 이 주소의 최종 디렉터리 이름도 manifest 식별자와 비교한다.

```bash
python -m services.document_loader.cli validate \
  --kind sec --input /tmp/sec-filing-mirror \
  --source-uri 'hdfs://namenode.example.internal:9000/data-lake/raw/realtime/sec-edgar/filing_date=2026-09-15/cik=1069183/accession=0001193125-26-391320'
```

## 입력별 완료 규약

`--input`에는 상위 데이터 루트가 아닌 아래 단위 하나를 지정한다. staging·임시 경로와 경로 탈출 참조는 거부한다.

| `--kind` | 입력 단위 | 완료 판정·검증 |
|---|---|---|
| `news` | `topic=.../partition=.../start=...` 최종 디렉터리 | 원자적 rename으로 게시된 `_manifest.json`. Parquet 해시·크기·행 수, Kafka offset 범위, 지역·날짜·원본 이벤트 일치 확인 |
| `news-analyzed` | `/data-lake/analyzed/news/company-mentions/model_version=.../topic=.../partition=.../start=...` | 빈 `_SUCCESS`, `_manifest.json`의 원본 배치·모델·사전 identity, `data.parquet` 해시·크기·행 수·offset·원문 projection·기업 결과 검증 |
| `dart` | `/datasets/opendart/snapshots/<snapshot>` | `ready.json`이 가리키는 manifest/checksums 해시, 문서 index 해시·건수, 원문 TAR·파싱 파일 크기 확인 |
| `sec` 또는 `sec-incremental` | `filing_date=.../cik=.../accession=...` | 빈 `_SUCCESS`, `_manifest.json`, metadata 해시·식별자, 원문 크기·해시 참조 일치 확인 |
| `sec-batch` | 과거 SEC 완료 배치 디렉터리 | 빈 `_SUCCESS`, manifest, `filings.jsonl` 건수·식별자·원문 경로·크기 확인 |

- 뉴스에는 `_SUCCESS`가 없다. 최종 배치 디렉터리와 manifest가 게시 완료 규약이다. Writer의 quarantine 행은 offset 검증에 포함하지만 서비스 문서로 적재하지 않는다.
- DART 일별 스냅샷은 해당 날짜의 새 공시 delta다. 같은 날짜의 여러 완료 delta를 각각 처리한다. 원천 대기 공시는 완료 index에 포함되지 않으며 이 Loader가 새로 수집하지 않는다.
- 뉴스 Parquet는 본문을 포함하므로 읽어서 원본 이벤트와 본문 해시를 검증한다. DART·SEC는 대용량 원문 전체를 다운로드하지 않고 메타데이터 해시와 원문 파일 크기를 확인한다. 실행 결과의 `raw_body_hashes_verified=false`를 원문 전체 해시 재검증 성공으로 해석하지 않는다.
- 과거 SEC 배치 manifest에는 `filings.jsonl`을 묶는 SHA256이 없다. 별도로 확인한 해시를 `--expected-index-sha256`에 전달하면 index도 비교한다. 생략한 실행은 `metadata_hashes_verified=false`와 제한 설명을 반환한다. Loader가 이번에 계산한 해시만으로 생산 당시 원본과 동일함을 입증하지는 못한다.

```bash
python -m services.document_loader.cli validate \
  --kind sec-batch \
  --input 'hdfs://namenode.example.internal:9000/datasets/sec-edgar/snapshots/20260908-nasdaq100-10y' \
  --company-map /etc/cosmos/document-loader/company-map.json \
  --expected-index-sha256 '<independently-verified-64-character-sha256>'
```

## 기업·출처 연결

`company`의 `(market, stock_code)`가 유일한 ACTIVE 기업을 가리켜야 한다. 이 Loader는 회사 기준정보를 생성하지 않는다. 확인된 기업이 미등록·비활성이면 배치 전체를 실패시켜 누락 적재를 막는다.

- DART는 공시 index의 `stock_code`를 KOSPI 기업에 연결한다.
- SEC는 CIK와 수집 메타데이터의 단일 symbol을 NASDAQ 기업에 연결한다. 복수 symbol이나 symbol 누락은 명시적 회사 매핑이 필요하다.
- 운영 뉴스 입력은 AI가 완료 게시한 `news-analyzed`만 사용한다. 각 기업의 `(market, stock_code)`, confidence, model version, 분석 시각을 `company_document`에 반영한다. 기사 지역과 기업 시장은 독립적이므로 국내 기사에 NASDAQ 기업, 해외 기사에 KOSPI 기업도 연결할 수 있다.
- Kafka의 at-least-once 재전송으로 같은 `event_id`가 여러 offset에 있으면 AI가 최신 `(collected_at, offset)` 한 건만 게시한다. manifest의 `records + duplicate_records`가 raw `valid_records`와 같아야 하며 Loader는 중복 제거된 문서만 적재한다.
- `companies=[]`도 분석 완료 결과다. 기사는 `ANALYZED`로 저장하고 기존 수집 힌트 연결을 모두 제거한다. 기업 결과가 있으면 기존 NEWS 연결을 AI 목록과 정확히 동기화한다. 분석 전 raw `news` 입력은 수동 호환·진단용이며 운영 자동 탐색에는 사용하지 않는다.

회사 매핑 파일은 다음 구조다. DART 키는 8자리 회사코드, SEC 키는 10자리로 채운 CIK다. 아래 Alphabet의 두 share class 중 사용할 기업은 실제 `company` 기준정보와 팀 정책에 맞춰 지정한다.

```json
{
  "dart": {
    "00126380": {"market": "KOSPI", "stock_code": "005930"}
  },
  "sec": {
    "0001652044": {"market": "NASDAQ", "stock_code": "GOOGL"}
  }
}
```

`--company-map /path/company-map.json`으로 전달한다. DART→KOSPI, SEC→NASDAQ만 허용한다. 입력에 있는 DART 종목코드나 SEC symbol 목록과 충돌하는 매핑은 거부한다.

기본 출처 키·이름은 `news:<region>:<provider>`, `DART`, `SEC`다. `--register-sources`가 없으면 출처가 이미 등록돼 있어야 한다. 이 플래그를 사용하면 정규화 결과에 명시된 출처를 문서와 같은 트랜잭션에서 생성한다. 기존 이름의 출처 유형·URL·활성 상태가 다르면 자동 변경하지 않고 실패한다.

팀 DB의 기존 출처 이름에 맞추려면 `--sources-file`로 다음과 같이 재정의한다. 이 파일은 기본 설정 전체를 대체하므로 이번 입력에서 사용되는 **모든 출처 키**를 포함해야 한다.

```json
{
  "news:domestic:naver_news_search": {
    "name": "Naver News Search",
    "source_type": "NEWS",
    "base_url": null
  },
  "dart": {
    "name": "DART",
    "source_type": "DISCLOSURE",
    "base_url": "https://dart.fss.or.kr"
  }
}
```

## PostgreSQL 적재

먼저 신규 [V6 마이그레이션](../../BackEnd/src/main/resources/db/migration/V6__add_document_loader_and_sec_disclosure.sql)을 기존 Flyway 절차로 적용한다. V1–V5는 수정하지 않는다. V6는 SEC 식별자, 문서의 최근 수집 시각, 배치 체크포인트를 추가하고 기존 DART 입력의 기본값을 유지한다. DB 권한과 배포 설정은 [운영 문서](../../deploy/document-loader/README.md)를 참고한다.

접속 정보는 저장소 밖에서 `DOCUMENT_DATABASE_URL`로 제공한다. 예를 들어 비밀번호를 명령행에 쓰지 않고 권한 `600`인 passfile을 사용할 수 있다.

```bash
export DOCUMENT_DATABASE_URL='host=db.example.internal port=5432 dbname=cosmos user=document_loader passfile=/etc/cosmos/document-loader/pgpass'

# 실제 INSERT/UPDATE와 제약조건까지 실행한 후 전체 롤백한다.
python -m services.document_loader.cli load \
  --kind dart --input 'hdfs://namenode.example.internal:9000/datasets/opendart/snapshots/<completed-snapshot>' \
  --register-sources

# 검증한 같은 입력을 영구 반영한다.
python -m services.document_loader.cli load \
  --kind dart --input 'hdfs://namenode.example.internal:9000/datasets/opendart/snapshots/<completed-snapshot>' \
  --register-sources --commit
```

기본 `load`는 읽기 전용 점검이 아니라 **실제 쓰기를 실행하고 롤백하는 검증**이므로 쓰기 권한과 행 잠금이 필요하다. 출처 생성·문서·기업 연결·체크포인트가 함께 롤백된다. `--commit`을 명시해야 반영된다. 결과 상태는 `validated`, `dry_run`, `loaded`, `already_loaded`, `error`다. 성공은 종료 코드 0, 입력·적재 실패는 2다.

## 중복·갱신·복구 규칙

| 문서 | 자연키 |
|---|---|
| 뉴스 | canonical URL의 SHA256. scheme/host 소문자화, fragment 제거, 기사 ID가 담긴 query는 보존 |
| DART | DART 접수번호 |
| SEC | `(10자리 CIK, accession number)`. 동일 accession을 여러 CIK가 공유할 수 있음 |

- 기존 자연키의 문서 UUID를 재사용한다. 신규 문서는 자연키로부터 안정적인 UUID를 만든다.
- AI 뉴스 결과는 `source_document.status='ANALYZED'`와 `analysis_version`을 기록한다. 기업 연결에는 `confidence`, `model_version`, `analyzed_at`을 UPSERT하고 분석 결과에 없는 기존 NEWS 연결은 삭제한다. 이후 raw 수집 입력이 재실행돼도 분석 완료 기사의 연결을 되살리지 않는다.
- 한 기사에 저장된 `analysis_version`과 다른 결과는 자동으로 교체하지 않고 명시적 재분석 절차를 요구한다. 동일 `analysis_version`과 동일 본문의 결정적 재실행만 허용한다. 이 규칙은 `companies=[]` 뒤에 과거 분석이 기업을 되살리는 것을 막는다.
- 같은 기업 연결을 새 NER 결과로 갱신할 때 운영자가 숨긴 `is_service_visible=false`는 유지한다. 기존 relevance·sentiment·impact 값은 다른 모델 provenance와 섞이지 않도록 NULL로 초기화한다.
- 완료 입력 URI에서 배치 ID를 만들고, 소비한 메타데이터 해시 및 정규화·출처 설정 해시를 체크포인트에 함께 저장한다. 같은 입력·설정의 재실행은 `already_loaded`이며 문서와 연결을 중복 생성하지 않는다.
- 이미 처리한 배치의 내용·설정이 달라지면 실패한다. 체크포인트를 지워 재실행하는 방식으로 충돌을 숨기지 않는다.
- 회사 매핑, 문서 상세, 기업 연결, 체크포인트 중 어느 단계라도 실패하면 해당 배치 전체가 롤백된다. 같은 스키마의 Loader 실행은 트랜잭션 잠금으로 직렬화된다.
- `last_collected_at`보다 오래된 입력은 최신 본문·제목을 덮어쓰지 않는다. 최초 수집 시각은 더 이른 관측으로 보완할 수 있다. 오래된 다른 본문에서 온 기업 연결도 새로 붙이지 않는다.
- 뉴스의 같거나 더 최근 수집 시각의 입력에서 기존 본문과 해시가 달라지면 COLLECTED 상태라도 실패한다. 기존 기업 연결을 다른 본문의 근거로 남기지 않도록 문서 revision·재분석 절차를 먼저 정해야 한다. 분석이 완료됐거나 정제 원문·분석 버전이 있는 공시도 본문 해시가 바뀌면 실패한다. 같은 수집 시각에 서로 다른 본문 해시가 들어와도 실패한다.
- 연결 오류로 커밋 응답을 받지 못했다면 같은 입력을 재시도해 체크포인트로 실제 반영 여부를 확인한다.

DART의 원문 참조는 `...tar#documents/<접수번호>.zip`, SEC는 `raw.txt`, 뉴스는 해당 Parquet 경로다. 개별 문서는 DB의 URL·식별자·해시와 함께 구분한다. HDFS 원문을 이 Loader가 수정하거나 삭제하지 않는다.

## 테스트

실제 HDFS 표본·PostgreSQL·Flyway 검증 결과는 [검증 기록](VERIFICATION.md)을 참고한다.

```bash
python -m unittest discover -s tests -v
```

DB 없이도 입력 검증·정규화·CLI 테스트를 실행할 수 있다. PostgreSQL 통합 테스트는 `TEST_DATABASE_URL`이 있을 때 실행하며, 무작위 테스트 스키마를 생성해 V1–V6 SQL을 적용한 후 정리한다. 실제 Flyway 엔진 검증과 SQL 직접 실행 테스트는 별개다. `TEST_DATABASE_URL`에는 격리된 테스트 DB를 지정한다.

## 운영 범위

단일 입력 CLI와 새 입력을 탐색하는 `services.document_loader.runner`를 제공한다. 자동 실행은 [운영 문서](../../deploy/document-loader/README.md)의 service·timer로 연결한다. PostgreSQL 성공 체크포인트가 있는 입력을 건너뛰고, 실패 입력을 재시도하면서 다른 입력도 계속 처리한다. 전체 초기 적재가 끝났는지는 실행 성공 여부와 별개로 대기 입력 수까지 확인해야 한다. 외부 알림 채널은 별도 운영 설정이 필요하다.

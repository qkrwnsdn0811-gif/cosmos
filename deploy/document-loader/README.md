# 문서 Loader 운영 실행 예제

국내·해외 뉴스, DART·SEC의 완료 입력을 PostgreSQL에 적재한다. 단일 입력용 oneshot과 새 입력을 탐색하는 자동 적재 서비스·timer를 제공한다. 파일을 저장소에 추가하는 것만으로 서버에 배포되거나 실행되지는 않는다.

데이터 규약과 CLI 전체 사용법은 [문서 Loader README](../../Crawling/documents/README.md)를 참고한다.

2026-09-16의 실제 DB 적용과 서비스 연결 내역은 [운영 반영 기록](OPERATIONS.md)에 정리했다.

## 구성 파일

| 파일 | 역할 |
|---|---|
| `run-loader.sh` | `DOCUMENT_KIND`, `DOCUMENT_INPUT`으로 CLI `load` 실행 |
| `common.env.example` | 코드·Python·Hadoop 경로, DB 접속 설정, 기본 롤백 설정 |
| `snapshot.env.example` | 실행할 입력 종류와 완료 HDFS 경로 |
| `cosmos-document-loader@.service` | `/etc/cosmos/document-loader/<instance>.env`를 읽는 oneshot 템플릿 |
| `discovery.example.json` | 완료 입력을 찾는 HDFS marker glob과 회사·출처 설정 |
| `cosmos-document-loader.service` | 한 번에 최대 50개 입력을 탐색·적재하는 자동 실행 작업 |
| `cosmos-document-loader.timer` | 부팅 후 1분, 직전 실행 종료 후 2분에 다시 실행 |
| `DocumentFlywayRunner.java` | 애플리케이션 Flyway 라이브러리로 V6 검증·적용·재실행 검사 |

기본값은 `DOCUMENT_COMMIT=0`, `DOCUMENT_REGISTER_SOURCES=0`이다. `0` 또는 `1`만 허용한다. 실제 커밋은 `DOCUMENT_COMMIT=1`, 없는 출처 생성은 `DOCUMENT_REGISTER_SOURCES=1`로 명시한다. 기본 실행에서도 실제 쓰기와 제약조건을 실행한 뒤 롤백하므로 쓰기 권한이 필요하다.

## 선행 조건

1. 별도 릴리스 경로에 `Crawling/documents/`와 `deploy/document-loader/`를 설치하고, 가상환경에 `requirements.txt`를 설치한다.
2. Hadoop CLI가 실제 NameNode 설정을 사용하도록 준비한다. 실행 계정에는 입력 HDFS 경로의 조회·읽기 권한이 필요하다.
3. PostgreSQL에는 V1–V5를 변경하지 않고 신규 [V6](../../BackEnd/src/main/resources/db/migration/V6__add_document_loader_and_sec_disclosure.sql)를 기존 Flyway 절차로 적용한다. 마이그레이션 계정과 Loader 실행 계정은 역할을 구분한다.
4. 입력이 참조하는 기업을 `company`에 등록하고 `(market, stock_code)` 및 ACTIVE 상태를 확인한다. Loader가 회사 기준정보를 생성하지 않는다.
5. 출처를 사전 등록하거나 명시적으로 `DOCUMENT_REGISTER_SOURCES=1`을 설정한다. 회사/출처 매핑은 CLI `validate` 출력과 비교한다.

서비스 파일은 `User=ubuntu`, `Group=ubuntu`를 가정한다. 실제 운영 계정이 다르면 서비스와 파일 소유권·passfile 접근 권한을 함께 조정한다.

## DB 권한 예시

아래는 이미 생성된 `document_loader` 역할이 `cosmos` DB의 `public` 스키마를 사용하는 예시다. 해당 DB에 접속한 관리자가 적용한다. 실제 역할·스키마·인증 정책에 맞춰 사용한다.

```sql
GRANT CONNECT ON DATABASE cosmos TO document_loader;
GRANT USAGE ON SCHEMA public TO document_loader;

GRANT SELECT ON public.company TO document_loader;
GRANT UPDATE (status) ON public.company TO document_loader;

GRANT SELECT ON public.data_source TO document_loader;
GRANT UPDATE (is_active) ON public.data_source TO document_loader;
-- --register-sources를 사용할 때 필요하다.
GRANT INSERT ON public.data_source TO document_loader;

GRANT SELECT, INSERT, UPDATE ON
    public.source_document,
    public.news_article,
    public.disclosure
TO document_loader;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.company_document TO document_loader;
GRANT SELECT, INSERT ON public.document_load_batch TO document_loader;
```

코드는 회사와 출처를 `SELECT ... FOR SHARE`로 잠근다. 이 잠금에는 SELECT 외에 최소 한 컬럼의 UPDATE 권한도 필요하므로 위 예시에 `company.status`, `data_source.is_active` 권한이 포함된다. Loader가 이 두 테이블의 기존 행을 UPDATE한다는 뜻은 아니다. `company_document`의 DELETE 권한은 AI가 분석한 기업 목록과 기존 뉴스 연결을 정확히 동기화할 때만 사용한다. TRUNCATE·스키마 변경 권한과 다른 테이블의 DELETE 권한은 필요하지 않다. [PostgreSQL 권한 문서](https://www.postgresql.org/docs/17/ddl-priv.html)를 참고한다.

이 예시는 일반 테이블 권한 기준이다. 별도 RLS나 접속 정책이 있다면 해당 정책도 만족해야 한다. 격리 테스트 스키마를 사용하는 테스트 계정은 테스트 스키마 생성·정리에 필요한 별도 권한이 필요하다.

## 서버 설정

코드·가상환경을 설치한 뒤 예제 파일을 다음 위치에 복사해 실제 값으로 작성한다. 접속 정보와 회사 매핑은 저장소 밖에 둔다.

```text
/opt/cosmos/document-loader/
  current/                              # 검토한 코드 릴리스
    Crawling/documents/
    deploy/document-loader/
  venv/
/etc/cosmos/document-loader/
  common.env
  news-batch-001.env                     # 인스턴스 이름에 대응
  company-map.json                       # 필요한 경우
  sources.json                           # 기존 출처 이름 재정의 시
  pgpass
```

`common.env` 예시:

```bash
DOCUMENT_PROJECT_DIR=/opt/cosmos/document-loader/current/Crawling/documents
DOCUMENT_PYTHON=/opt/cosmos/document-loader/venv/bin/python
HDFS_BIN=/opt/hadoop/bin/hdfs
DOCUMENT_DATABASE_URL="host=db.example.internal port=5432 dbname=cosmos user=document_loader passfile=/etc/cosmos/document-loader/pgpass"
DOCUMENT_COMMIT=0
DOCUMENT_REGISTER_SOURCES=0
# DOCUMENT_COMPANY_MAP=/etc/cosmos/document-loader/company-map.json
# DOCUMENT_SOURCES_FILE=/etc/cosmos/document-loader/sources.json
# DOCUMENT_EXPECTED_INDEX_SHA256=<independently-verified-legacy-sec-index-sha256>
```

passfile에는 PostgreSQL 형식으로 호스트·포트·DB·계정·비밀번호를 등록하고 권한을 `600`으로 설정한다. 실행 계정이 파일을 읽을 수 있어야 한다. 실제 비밀번호나 접속 문자열을 Git, 서비스 로그, 명령행 인자에 넣지 않는다.

`news-batch-001.env` 예시:

```bash
DOCUMENT_KIND=news-analyzed
DOCUMENT_INPUT=hdfs://namenode.example.internal:9000/data-lake/analyzed/news/company-mentions/model_version=dict-v1.3/topic=news.raw/partition=0/start=00000000000000000000
```

Hadoop 환경 설정이 기본 위치에 없다면 해당 서버의 `HADOOP_CONF_DIR`, `JAVA_HOME` 등도 실행 환경에 지정한다. 서비스는 `/usr/bin/bash`로 `run-loader.sh`를 실행하므로 Bash 설치와 스크립트 읽기 권한을 확인한다.

## 수동 검증·반영 순서

1. `cli validate`로 실제 완료 입력, 문서 수, 회사 키, 출처, 해시 검증 범위를 확인한다. 이 단계에는 DB 접속이 필요하지 않다.
2. `DOCUMENT_COMMIT=0` 상태에서 서비스 또는 CLI `load`를 실행한다. 실제 SQL 실행 후 회사·출처·문서·체크포인트가 롤백되는 결과를 확인한다.
3. 반영할 같은 입력과 설정을 유지한 채 해당 인스턴스 설정에 `DOCUMENT_COMMIT=1`을 지정한다. 필요한 경우에만 출처 등록을 활성화한다.
4. 같은 입력을 다시 실행해 `already_loaded`와 체크포인트를 확인한다. 데이터 내용을 바꾸고 기존 배치 ID를 재사용하지 않는다.

서비스 파일을 설치한 환경에서 수동 실행하는 명령은 다음과 같다.

```bash
sudo systemctl daemon-reload
sudo systemctl start cosmos-document-loader@news-batch-001.service
sudo systemctl status cosmos-document-loader@news-batch-001.service
sudo journalctl -u cosmos-document-loader@news-batch-001.service --no-pager -n 50
```

oneshot은 실행 후 종료되므로 `inactive (dead)`만으로 실패라고 판단하지 않는다. 종료 상태와 JSON 결과를 함께 확인한다. `TimeoutStartSec=30min`은 예제의 실행 상한이며 실제 입력 규모에 맞춰 검토한다.

`run-loader.sh`는 회사 매핑·출처 재정의 옵션을 전달한다. 과거 SEC index의 별도 검증 해시는 `DOCUMENT_EXPECTED_INDEX_SHA256`으로 설정하면 `--expected-index-sha256`에 전달된다. 로컬 미러용 `--source-uri`가 필요하면 현재 예제에서는 CLI를 직접 실행한다.

## 장애·재시도

- 미등록/비활성 회사, 출처 설정 충돌, 완료 표식·해시·건수 불일치는 원인을 수정한 뒤 같은 입력으로 다시 검증한다.
- 문서·상세·기업 연결·배치 체크포인트는 한 트랜잭션이다. 일부 행만 성공시켜 다음 입력으로 넘기지 않는다.
- 커밋 응답이 불확실한 연결 장애에서는 동일 입력·설정의 재실행으로 체크포인트를 확인한다.
- 뉴스는 같거나 더 최근 수집 시각의 입력에서 본문 해시가 달라지면 COLLECTED 상태라도 실패한다. 분석된 공시의 본문 변경도 실패한다. 오래된 입력은 최신 본문을 덮어쓰지 않으며 동일 본문 해시일 때만 기업 연결을 보완한다. 문서 revision·재분석 절차를 마련해야 하며, 강제로 체크포인트나 분석 행을 지워 우회하지 않는다.
- 과거 SEC index에 생산 당시 checksum이 없는 제한과 DART·SEC 원문 전체 해시를 다시 읽지 않는 검증 범위를 결과의 `verification`에서 구분한다.

## 새 입력 자동 탐색·적재

`discovery.example.json`의 NameNode 주소와 대상 경로를 확인해 `/etc/cosmos/document-loader/discovery.json`에 설치한다. 뉴스는 AI 분석 출력의 `_SUCCESS`, DART는 `ready.json`, SEC는 `_SUCCESS`가 있는 최종 입력만 발견한다. 운영 자동 뉴스 루트에는 raw `news`를 함께 넣지 않는다. SEC 과거 배치는 명시적으로 선택한 루트만 허용한다.

수동으로 자동 탐색의 한 회차를 검증할 수 있다. 기본값은 SQL 실행 후 롤백이며, 아래 `--state-file`은 운영 실행용 파일과 구분한다.

```bash
python -m services.document_loader.runner \
  --config /etc/cosmos/document-loader/discovery.json \
  --state-file /tmp/document-loader-dry-run-state.json \
  --max-batches 4 --dry-run
```

DB와 입력 검증을 마친 후 서비스·timer 파일을 `/etc/systemd/system/`에 설치한다. **자동 서비스는 `--commit`을 명시해 실제 적재한다.** 단일 입력 wrapper의 `DOCUMENT_COMMIT=0`과 별개다.

```bash
sudo systemctl daemon-reload
sudo systemctl start cosmos-document-loader.service --no-block
sudo systemctl enable --now cosmos-document-loader.timer
sudo systemctl list-timers cosmos-document-loader.timer
sudo journalctl -u cosmos-document-loader.service --no-pager -n 30
```

한 실행이 끝난 뒤 2분 후 다시 실행한다. 실행 중에는 같은 서비스가 중복 시작되지 않는다. 초기 대기분이 크거나 공시 스냅샷이 크면 한 회차가 오래 걸릴 수 있으므로 이는 입력마다 2분 이내 적재를 보장하는 설정은 아니다.

- 성공 여부는 PostgreSQL `document_load_batch`가 결정한다. 이미 성공한 입력은 다시 내려받지 않는다.
- 각 입력은 독립 트랜잭션이다. 실패한 입력을 기록하고 다른 입력을 계속 처리한다. 실패 입력에 재시도 기회를 배정하며 여러 출처를 번갈아 처리한다.
- `/var/lib/cosmos-document-loader/runner-state.json`은 실패·시도 순서와 설정 해시를 보관한다. DB 성공 체크포인트를 대체하지 않는다.
- `/var/lib/cosmos-document-loader/last-run.json`과 journal에서 `failed`, `discovery_errors`, `deferred`, `inputs`를 확인한다. `status=complete`는 해당 회차가 성공했다는 뜻이다. `deferred`가 남아 있으면 전체 초기 적재가 끝난 상태가 아니다.
- 입력은 게시 후 불변이라는 생산 규약을 따른다. 기존 입력의 내용까지 다시 검증하려면 단일 입력 CLI를 실행한다.
- 회사·출처·루트 설정 또는 Loader의 계약·HDFS 검증·PostgreSQL 쓰기·discovery 코드가 바뀌면 기존 상태 파일로 실행하지 않고 실패한다. 기존 영수증과 새로운 설정의 일치 여부를 단일 입력 CLI로 검토한 후 새 상태 파일을 선택한다. 재배포 시 상태를 자동 삭제하지 않는다.
- 오류가 있으면 서비스는 실패 종료해 운영 상태에 드러나며 timer가 다음 회차를 실행한다. 외부 메시지·알림 채널은 이 구성에 포함하지 않는다.

정지할 때는 timer를 먼저 중지한 뒤 실행 중인 서비스를 중지한다. 진행 중인 한 입력은 DB 트랜잭션으로 롤백되며, 이미 성공한 입력은 유지된다.

```bash
sudo systemctl disable --now cosmos-document-loader.timer
sudo systemctl stop cosmos-document-loader.service
```

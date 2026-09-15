# HDFS 집계 결과 → PostgreSQL RDB Loader

`AI/rdb_loader`는 완료된 관계 점수 스냅샷을 검증한 뒤 PostgreSQL에 발행한다.
`news_score`, `disclosure_score`, 기본 `score`를 현재 점수와 이력에 함께 저장한다.
사용자별 가중치나 계산 결과를 PostgreSQL/Redis에 기록하지 않는다.

```text
정규화된 문서별 관계 특징 Parquet
  → Spark: 출처별 점수 × 7D/30D/90D 집계
  → HDFS: data/*.parquet + manifest.json + _SUCCESS
  → Hadoop Master: python -m AI.rdb_loader
  → PostgreSQL: 관계 연결 → 이력 저장 → 현재 점수 교체 → PUBLISHED
```

## 1. 먼저 준비할 것

- BackEnd Flyway 마이그레이션을 적용한다. 현재 저장소의
  `V4__personalize_relationship_score.sql`이 구성 점수 컬럼을 추가한다.
  `V5__add_graph_snapshot_load_receipt.sql`은 재실행 검증용 적재 기록 테이블을 추가한다.
  기존 Flyway 파일을 고쳐서 재적용하지 않는다.
- `company`, `relationship_type`의 기준 데이터가 있어야 한다. 입력의 기업 UUID와 관계 유형이
  실제 DB와 일치해야 하며, 모르는 ID를 조용히 제외하지 않는다.
- Hadoop Master 서비스 계정이 입력 HDFS 디렉터리를 읽을 수 있어야 한다.
- Master에서 PostgreSQL에 연결할 수 있어야 한다. 아래의 운영 연결 설정을 확인한다.
- Python 3.10 이상, Hadoop CLI, Java를 준비한다. 코드는 저장소 루트에서 실행한다.

```bash
python3 -m venv .venv-rdb-loader
.venv-rdb-loader/bin/python -m pip install -r AI/rdb_loader/requirements.txt
.venv-rdb-loader/bin/python -m AI.rdb_loader --help
```

## 2. AI 입력과 점수 계산

새 Spark producer는 이미 정규화된 **문서별 관계 특징**을 입력으로 받는다.
기존 `AI/graph`의 전체 기간/30D 시드 점수를 뉴스·공시 점수로 복제하지 않는다.
문서 추출 단계에서 실제 기업·문서 UUID 및 비교 가능한 0~100 점수를 제공해야 한다.

| 필드 | 내용 |
| --- | --- |
| `source_company_id`, `target_company_id` | DB에 존재하는 기업 UUID |
| `relationship_type` | `SUPPLY`, `INVEST`, `PARTNER`, `COMPETE` |
| `document_id` | 출처 문서 UUID; 같은 문서를 중복 집계하지 않는 기준 |
| `document_type` | `NEWS` 또는 `DISCLOSURE` |
| `score` | 문서별 관계 점수, 0~100 |
| `published_at` | 시간대를 포함하는 시각; Spark timestamp는 UTC로 처리 |
| `confidence` | 선택, 0~1 |
| `impact_direction` | 선택, `POSITIVE`, `NEGATIVE`, `NEUTRAL`, `MIXED` |

`evidence-mean-v1`은 관계·출처별 고유 문서 점수의 산술평균을 사용한다.
같은 문서/관계의 동일 레코드는 한 번만 센다. 점수 등 내용이 다른 중복은 실패한다.
같은 문서가 여러 관계에 나타나도 출처 종류와 발행 시각은 일치해야 한다.
무방향인 `PARTNER`, `COMPETE`는 기업 UUID 순서로 정규화한다.

- 기간은 UTC 기준 `[as_of_at - 기간, as_of_at)`이며 `7D`, `30D`, `90D`를 계산한다.
- 두 출처가 있으면 `score = news_score × 0.5 + disclosure_score × 0.5`이다.
- 한 출처만 있으면 해당 점수를 기본 `score`로도 사용한다.
- 근거가 없는 출처는 `NULL`이다. 점수가 실제로 0인 근거와 구분한다.
- 양쪽 모두 근거가 없는 기간은 세 점수가 `NULL`이며, Loader가 검증 후 DB 적재 대상에서 제외한다.
- 점수는 소수점 여섯 자리까지 반올림한다.

## 3. HDFS 스냅샷 계약

스냅샷마다 새 UUID와 새 경로를 사용한다. 완료 경로의 파일은 수정하지 않는다.
Loader는 HDFS 디렉터리를 `hdfs dfs -get`으로 임시 디렉터리에 가져온 뒤 검사한다.

```text
relationship-scores/<snapshot UUID>/
├── data/
│   ├── part-00000-....parquet
│   └── part-00001-....parquet
├── manifest.json
└── _SUCCESS
```

`manifest.json`에는 다음 항목이 들어간다.

- `schema_version: 1`, `status: "SUCCEEDED"`, `snapshot_mode: "FULL"`
- `snapshot_id`, UTC `as_of_at`, `formula_version`, `model_version`, `hdfs_uri`
- `windows: ["7D", "30D", "90D"]`
- `record_count`, 기간별 `window_counts`
- `files`: `{ "path": "data/part-....parquet", "sha256": "..." }` 목록

Spark Parquet 출력 성공 후 manifest를 작성하고, **스냅샷 루트의 `_SUCCESS`를 마지막으로 쓴다**.
`data/_SUCCESS`만 있는 출력은 발행 가능한 스냅샷이 아니다.
Loader는 완료 여부, 파일 해시, 행 수, 기간, 중복 키, UUID, 수치 및 점수의 일관성을 검사한다.
부분 결과나 증분 스냅샷은 현재 점수 전체를 교체할 수 없으므로 받지 않는다.
원본 스냅샷에는 각 관계의 세 기간이 모두 있어야 한다. 짧은 기간에 근거가 없더라도
해당 행을 생략하지 않고 두 구성 점수/기본 점수 `NULL`, 근거 수 0으로 기록한다.

## 4. Spark 성공 뒤 Loader 연결

환경변수와 배포 경로를 준비한 Hadoop Master에서 `run-pipeline.sh`를 실행하면
Spark 완료 후 동일한 출력 경로를 Loader로 넘긴다. Spark가 실패하면 Loader를 호출하지 않으며,
어느 단계든 실패하면 스케줄러에 실패 종료 코드를 반환한다.
DB 환경변수는 스케줄러/서비스가 보호된 env 파일에서 주입한다.

아래의 `cosmos-master:9000`은 실제 NameNode 주소로 바꾼다.
producer 출력, Loader 입력, `RDB_LOADER_INPUT_ROOT`는 모두 같은 주소를 사용해야 한다.
호스트가 없는 `hdfs:///...` 축약 URI는 Loader 입력으로 받지 않는다.

```bash
cd /opt/cosmos/app
bash deploy/rdb-loader/run-pipeline.sh \
  --features hdfs://cosmos-master:9000/data-lake/processed/relation-features/run-20260915/data \
  --output-root hdfs://cosmos-master:9000/data-lake/processed/relationship-scores \
  --snapshot-id 10000000-0000-0000-0000-000000000001 \
  --as-of-at 2026-09-15T00:00:00Z \
  --model-version relation-features-v1
```

매번 새 snapshot ID와 실제 기준 시각을 지정한다. `SPARK_BIN`으로 Spark 실행 파일을 바꿀 수 있다.
이미 만들어진 완료 스냅샷의 적재 재시도에는 `run-loader.sh`를 직접 사용한다.
pipeline 전체를 같은 출력으로 다시 실행하면 producer가 기존 출력 디렉터리를 거부한다.

Spark와 적재를 서로 다른 작업으로 관리할 때의 개별 호출은 다음과 같다.

```bash
set -euo pipefail
cd /opt/cosmos/app
snapshot_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
snapshot_uri="hdfs://cosmos-master:9000/data-lake/processed/relationship-scores/${snapshot_id}"

spark-submit --master yarn --deploy-mode client \
  AI/rdb_loader/spark_aggregate.py \
  --input hdfs://cosmos-master:9000/data-lake/processed/relation-features/run-20260915/data \
  --output "$snapshot_uri" \
  --snapshot-id "$snapshot_id" \
  --as-of-at 2026-09-15T00:00:00Z \
  --model-version relation-features-v1

# 구조 검증은 DB 인증정보 없이도 실행할 수 있다.
bash deploy/rdb-loader/run-loader.sh --input "$snapshot_uri" --validate-only

# 아래 서비스가 /etc/cosmos/rdb-loader.env의 PostgreSQL 설정으로 적재한다.
sudo systemctl start "cosmos-rdb-loader@${snapshot_id}.service"
```

`--validate-only`는 입력 검증이며 DB의 기업/유형 존재 여부나 쓰기 권한까지 확인하지 않는다.
직접 실행할 때는 `DATABASE_URL` 또는 `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`,
`PGPASSFILE` 등을 실행 프로세스에 설정한 뒤 다음을 사용한다.

```bash
bash deploy/rdb-loader/run-loader.sh --input "$snapshot_uri"
# 로컬에서 미리 내려받은 동일한 완료 스냅샷도 지원한다.
.venv-rdb-loader/bin/python -m AI.rdb_loader --input /absolute/path/to/snapshot
```

검증 후 적재 대상이 0행이면 기본적으로 실패한다. 전체 그래프를 의도적으로 비우는
완료 스냅샷에만 직접 실행 시 `--allow-empty`를 추가한다. systemd 서비스에는 이 옵션을 넣지 않는다.

## 5. 운영 PostgreSQL 연결과 systemd 설치

### 같은 호스트의 Docker PostgreSQL

기본 `deploy/compose.yml`의 PostgreSQL은 Docker 내부 네트워크에만 있다.
Hadoop Master와 DB가 같은 Linux 호스트일 때만 아래 override로 loopback 포트를 연다.
서비스를 자동 재배포해도 설정이 유지되도록 `deploy/scripts/deploy.sh`가 이 설치 파일을
매번 읽고 릴리스 디렉터리에 함께 보관한다. 파일이 없으면 기존 배포 구성을 사용한다.

```bash
sudo install -o root -g root -m 0644 \
  deploy/rdb-loader/compose.override.yml /etc/cosmos/rdb-loader.compose.yml
```

다음 정상 애플리케이션 배포부터 override가 적용된다. 즉시 적용해야 할 때는 현재 릴리스의
동일한 `IMAGE_TAG`와 운영 환경 파일을 사용하여 PostgreSQL만 재조정한다.
컨테이너가 다시 만들어질 수 있으므로 운영 배포 절차에 포함한다.

```bash
# IMAGE_TAG는 실제 현재 릴리스 값이어야 한다.
docker compose --env-file /etc/cosmos/app.env -p cosmos \
  -f deploy/compose.yml -f /etc/cosmos/rdb-loader.compose.yml \
  up -d --wait --wait-timeout 90 postgres
```

기본 바인딩은 `127.0.0.1:5432`이다. 포트 충돌 시 `/etc/cosmos/app.env`의
`RDB_LOADER_PG_HOST_PORT`와 Loader의 `PGPORT`를 같은 값으로 바꾼다.
override 설치 경로는 배포 프로세스의 `COSMOS_RDB_LOADER_COMPOSE_OVERRIDE`로 바꿀 수 있다.
별도 Master에서 접근하는 Docker PostgreSQL도 이 override를 사용한다. 게시 주소를 Main의
Tailscale IP로 지정하고 Master만 허용하는 UFW·Docker guard를 적용하는 절차는
[Tailscale 운영 연결](../deploy/rdb-loader/tailscale-access.md)을 따른다.

### Loader 계정과 서비스

DB 관리자가 Loader 계정을 준비한다. Loader에는 대상 테이블 조회 및 관계/스냅샷/현재 점수/
이력/`graph_snapshot_load` 쓰기, 현재 점수 교체, 임시 테이블 생성에 필요한 권한이 있어야 한다.
계정 생성과 비밀번호는 배포 환경에서 관리한다.

서비스 예제는 `/opt/cosmos/app`에 배치한 저장소와 Hadoop 계정 `ubuntu`를 사용한다.
다른 경로/계정이면 unit의 `User`, `Group`, `WorkingDirectory`, `ExecStart`와 env를 함께 바꾼다.

```bash
sudo install -o root -g ubuntu -m 0640 \
  deploy/rdb-loader/rdb-loader.env.example /etc/cosmos/rdb-loader.env
sudo install -o root -g root -m 0644 \
  deploy/rdb-loader/cosmos-rdb-loader@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

실행 전 `/etc/cosmos/rdb-loader.env`의 DB 주소/계정, Hadoop/Java 및 Python 경로를 실제 값으로
수정하고, `PGPASSFILE`은 서비스 계정 소유의 `0600` 파일로 만든다.
스냅샷 기본 디렉터리 `RDB_LOADER_INPUT_ROOT`와 Spark `--output` 경로가 일치해야 한다.
예제 env에는 비밀번호가 없다. 비밀번호를 명령행, 로그, manifest에 넣지 않는다.

이 unit은 타이머 없이 스냅샷별로 실행하는 oneshot이다. Spark 완료 후 위 `systemctl start`
호출을 작업 스케줄러의 다음 단계로 연결한다. `systemctl start`는 적재 완료/실패까지 기다린다.

```bash
systemctl status "cosmos-rdb-loader@${snapshot_id}.service"
journalctl -u "cosmos-rdb-loader@${snapshot_id}.service" --no-pager
```

## 6. 적재 보장과 재실행

전체 스냅샷을 검증한 뒤 관계 연결, 이력 저장, 현재 점수 교체, `PUBLISHED` 전환을
하나의 PostgreSQL 트랜잭션에서 처리한다. 중간 오류는 이전 발행 그래프를 유지한다.
스냅샷 ID와 manifest 해시가 일치하는 재실행은 중복 발행하지 않는다.
다른 입력으로 같은 ID를 재사용하거나 최신 발행 시각 이하의 새 스냅샷을 발행하면 실패한다.
DB advisory lock으로 발행자를 직렬화한다. 새 결과는 새 스냅샷으로 발행한다.
개인화는 응답의 두 구성 점수를 이용해 FE가 계산한다.

실패 시 먼저 종료 로그를 확인한다. `_SUCCESS`/manifest/해시 오류는 생산 단계를,
알 수 없는 기업·관계 유형은 기준 데이터를, DB 연결 오류는 운영 env와 네트워크를 수정한다.
완료 스냅샷 자체를 고쳐서 같은 ID로 재발행하지 않는다.

## 7. 검증 명령

```bash
python -m unittest discover -s AI/rdb_loader/tests -v
bash deploy/tests/deploy-test.sh
bash deploy/rdb-loader/tests/pipeline-test.sh
```

PostgreSQL 통합 테스트는 임시 테스트 DB만 사용한다.
`TEST_DATABASE_URL`을 지정하면 DB 통합 검증을 실행할 수 있다.
운영 DB를 테스트 대상으로 지정하지 않는다.

로컬 Spark 계산·파일 해시·manifest 쓰기 검증은 운영과 같은 Spark 버전 및 전체 JDK를 준비하고
`RDB_LOADER_SPARK_TESTS=1`로 실행한다. Loader 자체에는 PySpark 설치가 필요하지 않다.

```bash
python -m pip install pyspark==4.2.0
RDB_LOADER_SPARK_TESTS=1 python -m unittest AI.rdb_loader.tests.test_spark -v
```

2026-09-15 로컬 검증: Python/실제 PostgreSQL 17.10 검사 43개, Spark 4.2.0·JDK 21 검사 5개,
Spring 그래프 테스트 12개가 통과했다. 배포 스크립트 10개와 Spark→Loader 연결 4그룹도 통과했다.
HDFS 다운로드는 CLI 대역으로, Spark 계산과 Hadoop 파일 해시·쓰기 함수는 로컬 모드로 확인했다.

## 8. 운영 적용 확인

2026-09-15 실제 HDFS와 운영 PostgreSQL 17.11의 격리 스키마에서 적재·재실행·실패 롤백과
테스트 API 응답을 확인했다. 운영 `public` 스키마에는 실제 Flyway 12.4.0으로 V5를 적용했고,
Master 전용 접속과 Loader systemd unit을 설치했다. 상세 결과와 정리 확인은
[운영 검증 기록](rdb-loader-operating-verification.md)에 있다.

별도 실행이 필요한 Flyway 도구는 Python 3.11 이상과 전체 JDK 21을 사용한다.
`migrate-schema.py`는 실행 중인 앱의 라이브러리와 V1~V4 파일을 대조하고, `--apply`를
지정하면 제한된 경로에 백업 후 적용·재검증·재실행을 수행한다. 옵션 없이 실행하면 검증만 한다.

```bash
python3 deploy/rdb-loader/migrate-schema.py \
  --migration-dir BackEnd/src/main/resources/db/migration \
  --work-dir /home/ubuntu/cosmos-rdb-loader-migration-check
```

실제 분석 산출물과 기준정보가 준비되면 위 pipeline 또는 스케줄러에 연결해야 한다.
이번 집계는 Master의 Spark `local[2]`로 실제 HDFS에 출력했다. 중지된 NodeManager를 사용하는
YARN 분산 실행과 실제 운영 그래프 게시 검증은 후속 작업이다.

# 뉴스 수집 재개와 Kafka → HDFS 운영

2026-09-15에 SSAFY Hadoop Master `j15c205a.p.ssafy.io`에서 실행하도록 구현했다. 기존 AWS ECS/RDS 인증정보를 요구하지 않는다. 이 문서는 당시 확인한 수집 경로의 운영 방법이며, 배포 경로는 이 폴더의 예제 래퍼 기준으로 표기한다. 실제 서버의 기존 unit은 이번 소스 정리에서 변경하지 않았다. 아래 서비스 전환 명령은 상태와 선행조건을 확인한 운영자가 수행한다. 이 문서에서 기존 MySQL HDFS 미러는 보존된 과거 입력이다.

## 실행 구성

```text
국내 수집기 / Yahoo 수집기
 → 영속 URL 큐 + SQLite outbox
 → Kafka news.raw (3 partitions, replication 1)
 → cosmos-news-hdfs-writer
 → HDFS Parquet (현재 Worker 3대, HDFS replication 2)
```

수집과 Kafka·Writer는 Master에서 실행한다. Hadoop Worker의 small 사양이나 디스크 크기를 변경하지 않는다. Spark/AI 분석 실행은 이 수집 서비스와 별도다.

| 서비스 | 역할 | 자원 제한 |
|---|---|---|
| `cosmos-news-collector.service` | 최신 뉴스·국내 원천·연도별 백필을 번갈아 실행하고 outbox를 Kafka에 전달 | CPU 1코어 상당, RAM 최대 1GiB |
| `cosmos-news-hdfs-writer.service` | HDFS 저장 검증 후 Kafka offset 기록 | CPU 1코어 상당, RAM 최대 1.5GiB |

프로그램은 `/home/ubuntu/news-kafka/app`, Python venv는 `/home/ubuntu/news-kafka/venv`에 있다. Node 22.23.2는 전용 runtime 디렉터리에 두었고 공식 SHA-256을 검증했다. 서비스 등록 후 부팅 시 자동 실행한다. 수집기는 한 프로세스로 호출당 최대 8건을 처리한다. Yahoo는 같은 호스트에 최소 4.5초 간격을 두며 국내 원천도 기존 robots 정책과 원천별 요청 간격을 지킨다.

## 복구한 수집 범위

- Yahoo Finance: 최신 3일 반복 탐색과 2026-09-09 이후 초기 공백 탐색을 구분한다. 별도로 2016–2026 연도별 큐를 순환하며 과거 목록을 다시 탐색한다.
- 국내 고정 원천: 한국정경신문, 메디컬투데이, 서울경제, 뉴스핌, 뉴스토마토의 공개 사이트맵/목록.
- HelloT: 기존 설정의 기사 번호 10000–114676 범위를 이어서 탐색한다. 이 번호보다 높은 최신 기사까지 수집하는 설정은 아니다.
- 기존 RDS에서만 관리한 동적 언론사 목록과 전체 10년 백필 상태는 회수하지 못했다. 위 고정 원천 이외의 수집 범위까지 복원했다고 해석하지 않는다.
- OpenDART·SEC 수집기를 변경하는 작업은 이번 뉴스 연결 범위에 포함하지 않는다.

기존 EFS 연도별 큐와 MySQL의 마지막 진행 기록은 확보되지 않았다. 따라서 마지막 커서를 그대로 복원하는 대신 아래 두 HDFS 보존본의 URL을 SQLite 색인으로 읽고, 이미 확보한 URL의 본문 다운로드를 건너뛴다.

```text
/datasets/news/live/mysql_overseas/data
/datasets/news/snapshots/20260908-prepared/news
```

과거 뉴스 발행일과 마지막 HDFS 동기화 시각은 별개다. 9월 12일까지 발행된 모든 기사가 완료된 것으로 간주하지 않는다. 기존 AWS에만 남은 미반영 데이터나 실패·대기 목록까지 복구됐다고 주장하지 않는다.

## 기존 URL 색인 준비와 전체 수집 전환

새 환경에서 상태 DB를 복원하거나 기존 HDFS 보존본으로 수집을 재개할 때는 전체 수집기를 시작하기 전에 URL 색인을 준비한다. 이 작업은 기존 기사 본문을 Kafka로 재전송하는 작업이 아니라, HDFS의 URL 컬럼을 읽어 이미 확보한 기사의 재다운로드를 줄이는 작업이다.

```bash
/home/ubuntu/news-kafka/app/deployment/run-news-python.sh -m services.news_pipeline.seed_existing \
  --state-dir /home/ubuntu/news-kafka/state
```

기본 입력은 위 두 HDFS 경로다. 100,000행 단위로 정렬한 해시를 SQLite에 커밋하고, 파일 전체를 읽고 검증한 뒤 `outbox.db`의 `seed_files`에 완료 기록을 남긴다. 실행이 중단되면 같은 명령으로 재개한다. 크기가 같은 완료 파일은 건너뛰고 미완료 파일은 다시 읽으며, 이미 입력한 해시는 중복 삽입하지 않는다. 과거 원본과 HDFS 파일은 변경하지 않는다. `--max-files`를 사용한 제한 실행은 전체 완료가 아니다.

완료 판정은 `seed-status.json`의 `state=complete`, `limited=false`, 오류·미해결 파일 목록이 비어 있고 `unresolved_rows=0`인 경우다. 프로세스 종료 코드만으로 판정하지 않는다. `state=running`도 마지막 갱신 시각과 실행 프로세스를 함께 확인해야 한다. 이 색인은 보존본에 있는 URL 범위만 확인하며, 모든 과거 기사의 수집 완료를 보장하지 않는다.

전환 중 최신 기사부터 재개할 때는 일반 `cosmos-news-collector.service`를 정지해 두고 `--jobs latest`인 임시 `cosmos-news-latest-bootstrap.service`만 실행한다. 색인 완료 후 임시 서비스를 정지하고 일반 서비스를 시작한다. 둘은 같은 상태 디렉터리와 프로세스 잠금을 사용하므로 동시에 실행하지 않는다.

```bash
sudo systemctl stop cosmos-news-latest-bootstrap.service
sudo systemctl enable --now cosmos-news-collector.service
```

**색인 완료에 따른 자동 전환은 구현하지 않았다.** 일반 서비스의 기본 작업은 `latest,domestic,backfill`이며 색인 상태를 자체 검사하지 않는다. 따라서 초기 배포나 상태 DB 복원 때 일반 unit을 설치하고 곧바로 시작하면 과거 색인이 준비되기 전에 백필이 시작될 수 있다. 전환 후 `collector-status.json`의 세 작업 결과와 Kafka 전달 결과, Writer의 HDFS 게시를 확인한다. 기존 상태 DB를 보존하는 단순 코드 재배포는 색인을 처음부터 다시 만들 필요가 없다.

## 메시지와 저장 경로

`news.raw` 한 토픽에서 `region=domestic|overseas`, `source`, `language`를 명시한다. Key는 `source:URL_SHA256`이다. `event_id`는 출처·URL·본문의 해시이며 `run_id`, 수집 시각, 원본 URL, 본문과 원천 메타데이터를 보존한다. 발행 시각의 시간대를 확인할 수 없으면 정규화 필드를 null로 두고 원래 값은 metadata에 보존한다. 국내 쿼리 문자열의 기사 번호는 지우지 않는다.

현재 수집은 URL별 최초 확보를 기준으로 한다. 기존 HDFS 색인 또는 outbox에 같은 URL이 있으면 본문 다운로드를 건너뛰므로, 같은 URL의 기사 본문이 나중에 수정돼도 자동으로 새 버전을 수집하지 않는다. 본문 해시를 포함한 `event_id`는 이러한 수정 감지를 대신하지 않는다. 수정 기사 수집에는 별도의 재방문·버전 관리 정책이 필요하다.

Writer는 수집일(UTC)과 국내/해외 구분으로 파일을 묶는다. 게시 원자성을 위해 Kafka 배치 디렉터리가 그 바깥에 위치한다.

```text
/data-lake/raw/realtime/news/topic=news.raw/
  partition=0/start=00000000000000000000/
    region=domestic/date=2026-09-15/part.parquet
    region=overseas/date=2026-09-15/part.parquet
    _manifest.json
```

기본 flush는 최대 2,000건/파티션, 전체 원본 버퍼 8MiB 또는 120초다. 실제 수집량이 적을 때는 시간 조건으로 작은 파일이 생성될 수 있다. 수집량을 측정한 뒤 주기를 조정하거나 별도 compaction으로 합친다.

Spark에서 정상 기사만 읽는 예:

```python
news = (spark.read
    .option("recursiveFileLookup", "true")
    .option("pathGlobFilter", "*.parquet")
    .parquet("hdfs://100.117.115.44:9000/data-lake/raw/realtime/news/topic=news.raw"))
domestic = news.filter("region = 'domestic'")
overseas = news.filter("region = 'overseas'")
unique_events = news.dropDuplicates(["event_id"])
```

옛 `/datasets/news/...`의 컬럼 구성과 새 스키마가 다르므로 정규화 없이 단순 union하지 않는다. 기존 경로와 새 경로를 함께 분석할 때 출처·정규화 URL·본문 해시로 중복을 정리한다. 검증용 토픽/데이터는 `cosmos.news.verify.*`와 `_verification` 하위에만 둔다. 실제 분석 입력은 위 `topic=news.raw` 경로다.

## 실패와 재시작

1. 후보 URL과 목록 커서를 로컬 SQLite에 영속화한다. 국내 목록 커서와 후보 저장은 한 트랜잭션이다.
2. 본문을 outbox에 저장한 뒤 후보를 수집 완료로 바꾼다.
3. Kafka ACK 후 outbox를 전송 완료로 바꾼다. 전송 완료 후에도 본문은 로컬 outbox에 남는다.
4. Writer가 숨겨진 HDFS 임시 배치에 Parquet·manifest를 쓰고 크기·SHA-256을 검증한다.
5. 배치 디렉터리를 한 번에 게시한 뒤 Kafka offset을 commit한다. 게시 후 응답이 유실돼도 재시작 때 HDFS manifest를 기준으로 복구한다.

동일 Kafka offset의 재처리는 파일을 중복 게시하지 않는다. 다만 Kafka ACK 직후 SQLite 갱신 전에 생산자가 중단되면 같은 이벤트가 다른 offset으로 다시 전달될 수 있다. 전체 시스템을 exactly-once라고 표현하지 않으며 정제 단계에서 `event_id` 기준 중복 처리가 필요하다.

Writer는 Kafka 보존기간 경과로 HDFS 체크포인트 다음 offset을 읽을 수 없거나, 토픽 재생성·로그 절단·체크포인트 불일치를 발견하면 중단한다. 예를 들어 `kafka_retention_gap_before_hdfs_checkpoint`는 누락 구간을 자동으로 건너뛰지 않았다는 뜻이다. 단순 재시작이나 임의 offset 변경으로 해결하지 말고 Kafka 범위·HDFS manifest·남아 있는 outbox를 대조해 복구 범위를 정해야 한다. 전송 완료 outbox 본문도 보존되지만, 이를 자동 재발행하는 장애 복구 절차는 구현하지 않았다. 기존 토픽이나 HDFS 배치를 삭제해 맞추지 않는다.

스키마 오류 메시지는 HDFS 배치의 `quarantine/invalid.jsonl`에 원문을 보존한다. `pipeline.dlq`에는 원문 위치·해시·출처 offset만 전달한다. DLQ는 파티션 1개, 복제 수 1, 보존기간 7일, 파티션 용량 기준 1GiB로 생성했다. 수집 단계에서 재시도 소진한 URL은 별도 로컬 큐에 남으며 Kafka DLQ로 자동 이동하는 대상은 아니다.

현재 Writer는 Master 한 곳의 단일 프로세스와 파일 잠금을 전제로 한다. 여러 서버에서 같은 HDFS 출력 경로를 동시에 쓰면 안 된다. Kafka partition 수를 바꾸면 Writer의 expected-partitions 검증과 배치·커서 운영을 함께 변경한다.

## 상태 확인

```bash
systemctl status cosmos-news-collector cosmos-news-hdfs-writer --no-pager
journalctl -u cosmos-news-collector -n 20 --no-pager
journalctl -u cosmos-news-hdfs-writer -n 20 --no-pager
cat /home/ubuntu/news-kafka/state/collector-status.json
cat /home/ubuntu/news-kafka/writer-state/status.json
cat /home/ubuntu/news-kafka/state/seed-status.json
/opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server 100.117.115.44:9092 \
  --group cosmos-news-hdfs-v1 --describe
```

Writer는 정적 파티션 할당을 사용하므로 Consumer Group이 `Empty`로 표시돼도 실행 중일 수 있다. 서비스 상태, 최근 상태 파일, HDFS manifest와 각 파티션의 committed offset/lag를 함께 확인한다.

`state/outbox.db`, `state/domestic.db`, `state/overseas/*.db`는 운영 상태이므로 지우지 않는다. SQLite 파일 백업은 실행 중 파일을 그대로 복사하지 말고 SQLite backup API로 일관된 사본을 만든다.

Kafka 장애 때 outbox는 미전송 상태를 유지한다. 대기 10,000건 초과, 누적 JSON payload 10GiB 초과 또는 Master 여유 디스크 20GiB 미만이면 신규 수집을 일시 중지하고 전송을 계속 시도한다. payload에는 본문과 메타데이터가 함께 들어가며, 10GiB 계산에는 전송 완료 행도 포함된다. SQLite의 URL 색인·내부 페이지·WAL 파일 크기는 이 합계에 포함되지 않으므로 실제 디스크 사용량과 다르다. 장기 운영 시 HDFS 검증 후 outbox 보존 정책을 별도로 정해야 하며, 이 구현은 원문을 자동 삭제하지 않는다.

`news.raw`의 현재 보존 설정은 48시간 및 파티션당 2GiB다. 용량 조건으로 더 일찍 정리될 수 있으므로 단순히 48시간의 장애 복구 여유가 있다고 가정하지 않는다. Kafka 복제 수 1은 Kafka 서버 장애의 이중화를 제공하지 않는다. HDFS 복제 수 2는 HDFS 저장 이후의 복제다.

## 재배포와 검증

`Crawling/news`에서 `npm ci` 후 `npm run build`로 국내 추출기를 번들링하고 `python scripts/package-news.py`로 허용된 코드만 묶는다. AWS 인증정보나 환경 파일은 배포 패키지에 넣지 않는다. Python 의존성은 `requirements.txt`에 명시했다.

Hadoop JVM이 Python 종료 신호 처리를 바꿀 수 있어 Writer는 HDFS 초기화 후 SIGTERM 핸들러를 등록한다. 통합 검증은 실제 종료·재시작까지 포함한다.

```bash
/home/ubuntu/news-kafka/app/deployment/run-news-python.sh scripts/verify_news_kafka_hdfs.py \
  --bootstrap 100.117.115.44:9092 \
  --app-root /home/ubuntu/news-kafka/app \
  --state-dir /home/ubuntu/news-kafka/verification
```

검증은 전용 토픽을 생성하고 증거를 보존한다. 프로덕션 `news.raw`에 가짜 기사를 넣지 않는다. 실제 실행 결과는 `verification/verify-result.json`과 배포 작업 보고서에 기록한다.

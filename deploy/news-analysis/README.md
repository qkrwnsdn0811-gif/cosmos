# Realtime news company analysis service

이 서비스는 완료된 raw 뉴스 배치를 발견해 `dict-v1.3` 기업 매처를 실행하고 정식 HDFS
analyzed 경로에 원자적으로 게시한다. PostgreSQL, Spring Boot, FrontEnd는 수정하거나 호출하지
않는다. 후속 RDB loader가 `_SUCCESS`가 있는 analyzed 배치만 읽는다.

## 데이터 흐름

```text
/data-lake/raw/realtime/news/topic=news.raw/partition=*/start=*/_manifest.json
  -> AI/ner/realtime_runner.py
  -> AI/ner/realtime_analyzer.py
  -> /data-lake/analyzed/news/company-mentions/
       model_version=dict-v1.3/topic=news.raw/partition=*/start=*/
         data.parquet
         _manifest.json
         _SUCCESS
```

runner는 raw marker와 analyzed `_SUCCESS`를 각각 HDFS 목록 한 번으로 읽는다. `_SUCCESS`가
있는 raw 배치는 `_manifest.json`의 model/code/aliases/companies identity가 현재 배포와 같은지
검증한 뒤 완료로 건너뛴다. 나머지는 파티션별 round-robin으로 최대
`NEWS_ANALYSIS_MAX_BATCHES`개 처리하므로 한 파티션의 물량이 다른 파티션을 막지 않는다.
이전에 실패한 입력은 pass quota의 최대 1/4만 재시도하고 아직 시도하지 않은 입력을 먼저
진행한다. 신규 입력이 줄면 나머지 quota도 실패 입력 재시도에 사용한다.
로컬 state에는 다음에 시작할 파티션만 기록하며 완료 여부는 항상 HDFS marker로 다시 판단한다.

분석기는 프로세스마다 하나의 libhdfs JVM을 사용하는 PyArrow HDFS client로 batch 파일을 읽고
쓴다. 배치 파일마다 `hdfs dfs` 프로세스를 실행하지 않는다. raw 파일의 manifest, SHA-256,
크기, Parquet 행 수, 전체 Kafka offset을 확인한다. 같은 `event_id`의 at-least-once 전달은 최신
`(collected_at, kafka_offset)`만 게시하고 manifest에 중복 수를 남긴 뒤 staging 디렉터리를 final
디렉터리로 rename한다.

## 설치

릴리스에는 최소한 아래 경로가 있어야 한다.

```text
/opt/cosmos/news-analysis/current/AI/ner/
/opt/cosmos/news-analysis/current/deploy/news-analysis/
/opt/cosmos/news-analysis/venv/
```

venv에는 Python 3.12와 `pyarrow`가 필요하다. `matcher.py`는 `pyahocorasick`이 없으면 동일한
정규식 backend를 사용하지만 운영 처리량을 위해 설치를 권장한다.

```bash
sudo install -d -o root -g ubuntu -m 0750 /etc/cosmos
sudo install -o root -g ubuntu -m 0640 \
  deploy/news-analysis/news-analysis.env.example /etc/cosmos/news-analysis.env
sudo editor /etc/cosmos/news-analysis.env

sudo install -o root -g root -m 0644 \
  deploy/news-analysis/cosmos-news-analysis.service /etc/systemd/system/
sudo install -o root -g root -m 0644 \
  deploy/news-analysis/cosmos-news-analysis.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

`NEWS_ANALYSIS_VERSION`은 배포한 `AI/ner`의 Git commit 또는 불변 release ID로 반드시 바꾼다.
aliases/companies 파일과 이 버전이 output identity에 포함되므로 같은 model 경로에 코드를 바꿔
조용히 덮어쓸 수 없다.

## 적용 전 검증과 활성화

timer를 켜기 전에 oneshot 서비스를 한 번 시작해 HDFS/JAVA/libhdfs 환경과 게시 계약을 확인한다.
systemd가 `/run/cosmos-news-analysis`와 `/var/lib/cosmos-news-analysis`를 서비스 계정 소유로 먼저
만들기 때문에 수동 wrapper보다 이 순서가 안전하다.

```bash
sudo systemctl start cosmos-news-analysis.service
sudo systemctl status cosmos-news-analysis.service --no-pager
sudo journalctl -u cosmos-news-analysis.service -n 100 --no-pager
sudo systemctl enable --now cosmos-news-analysis.timer
systemctl list-timers cosmos-news-analysis.timer --no-pager
```

oneshot 서비스가 실행되는 동안 systemd는 같은 unit의 중복 시작을 합치고, runner도 POSIX lock을
잡아 수동 실행과의 중복을 막는다. 실패한 배치는 `_SUCCESS`가 생기지 않아 다음 실행의 pending으로
남는다. 다른 배치는 같은 pass에서 계속 처리된다.

## 안전한 중지와 상태 확인

```bash
sudo systemctl disable --now cosmos-news-analysis.timer
sudo systemctl stop cosmos-news-analysis.service

sudo -u ubuntu /opt/hadoop/bin/hdfs dfs -ls \
  'hdfs://100.117.115.44:9000/data-lake/analyzed/news/company-mentions/model_version=dict-v1.3/topic=news.raw/partition=*/start=*/_SUCCESS'
```

로컬 `runner-state.json`을 잃어도 재처리 여부에는 영향이 없다. 파티션 시작 순서만 초기화되며,
이미 게시된 batch는 HDFS `_SUCCESS`로 다시 확인해 건너뛴다.

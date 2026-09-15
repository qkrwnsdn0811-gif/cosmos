# COSMOS 뉴스 수집 → Kafka → HDFS

국내 고정 매체, Yahoo Finance, 네이버 뉴스 API를 수집하고 로컬 영속 큐·outbox를 거쳐 Kafka `news.raw`에 전달한다. 별도 Writer가 국내/해외를 나눈 Parquet와 검증 manifest를 HDFS에 게시한다. 이 폴더만으로 빌드·테스트·패키징할 수 있으며 기존 AWS RDS나 MySQL 웹 스택은 필요하지 않다.

```text
국내 매체 / Yahoo Finance / 네이버 API 기업명 검색
  → SQLite 후보 큐·URL 색인·outbox
  → Kafka news.raw (3 partitions)
  → HDFS Writer
  → topic=news.raw/partition=.../start=.../region=domestic|overseas/date=.../
```

## 빌드와 오프라인 테스트

실제 서비스 실행 환경은 Linux, Python 3.11 이상(운영 3.12), Node 22.13 이상(운영 22.23.2), Java 17 및 Hadoop 3.5 클라이언트다. Windows에서도 아래 단위 테스트와 번들 빌드는 가능하며 서비스의 `fcntl` 잠금·systemd·libhdfs 운영은 Linux에서 수행한다. 명령은 `Crawling/news` 디렉터리에서 실행한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm ci
npm run build
python -m unittest discover -s tests -p 'test_news_kafka_*.py' -v
npm test
python scripts/package-news.py
```

PowerShell에서는 venv 생성 후 `.\.venv\Scripts\python.exe`를 `python` 대신 사용할 수 있다. `npm.cmd`로 npm을 실행한다. 테스트는 임시 로컬 SQLite·가짜 HTTP/Kafka/HDFS를 사용하며 실제 크롤링·DB 접속·Kafka 전송을 하지 않는다. PyArrow를 설치하면 실제 Parquet 직렬화 왕복 테스트도 수행한다. 네이버 추가 이후 검증 결과는 [NAVER.md](docs/NAVER.md)에 기록한다.

`npm run build`가 생성하는 `services/news_pipeline/domestic.bundle.mjs`, `naver-fetch.bundle.mjs`와 `dist/news-kafka.tar.gz`는 Git에서 제외된다. `package-news.py`는 명시한 허용 파일만 묶고 env·상태 DB를 수집하지 않는다. 배포 tar는 실행할 국내·네이버 번들을 포함한다. 소스만 checkout한 환경에서는 반드시 먼저 빌드한다.

## 현재 수집 범위와 한계

- 네이버 뉴스 API: 프로젝트 코스피 100종목의 기업명 검색, 최신순 주기 조회와 본문 수집을 별도 worker로 지원한다. [설정·주기·검증·운영 안내](docs/NAVER.md).
- 국내 운영 원천 5개: 한국정경신문, 메디컬투데이, 서울경제, 뉴스핌, 뉴스토마토의 공개 사이트맵·목록.
- Yahoo Finance: 최근 3일 반복 탐색, 2026-09-09 이후 초기 공백 탐색, 2016–2026 연도별 과거 목록 순환을 별도 큐로 처리한다. 연도 범위를 설정했다고 수집이 완료된 것은 아니다.
- HelloT 어댑터도 포함하지만 **기존 기사 번호 10000–114676을 탐색하는 과거 범위**다. 최신 목록 탐색기가 아니며 2026-09-15 확인 당시 일부 오래된 번호는 `article_not_found`로 재시도 중이었다. 국내 최신 원천 5개와 동일한 완료·신선도를 가정하지 않는다.
- 이전 AWS에만 있던 동적 매체 목록·EFS 커서·미반영 RDS 데이터까지 복구한 것은 아니다. 이미 HDFS에 보존된 URL은 색인 후 건너뛴다.
- 최신 뉴스도 주기적으로 수집·배치 게시한다. 수집 시각·기사 발행 시각·HDFS 게시 시각은 다르며 모든 매체의 모든 속보를 즉시 반영한다는 보장은 없다.

네이버 추가 전 2026-09-15 운영 확인에서는 HDFS 경로의 국내·해외 저장량이 142초 동안 각각 8행씩 실제 증가했다. 네이버 전용 서비스의 배포와 검증은 [NAVER.md](docs/NAVER.md)를 참고한다.

## 운영 설정과 시작 순서

Kafka 3파티션 `news.raw`, 1파티션 `pipeline.dlq`와 정상 Hadoop 클러스터·클라이언트 설정이 먼저 필요하다. 이 패키지는 EC2·Kafka·Hadoop을 자동 생성하지 않는다. 현재 운영 예제의 Kafka 주소는 `100.117.115.44:9092`, HDFS는 Hadoop 설정의 기본 파일시스템을 사용한다. HDFS Writer는 복제 수 2를 명시한다.

`HDFS_HOST`·`HDFS_PORT`는 URL Seeder에만 적용된다. **Writer의 접속 대상은 `HADOOP_CONF_DIR`의 `core-site.xml`에 있는 `fs.defaultFS`**이므로 새 서버에서는 이 Hadoop 설정을 별도로 확인해야 한다.

1. tar를 예를 들어 `/home/ubuntu/news-kafka/app`에 풀고 venv·Node·Hadoop 경로를 준비한다. 배포 래퍼의 실행 권한을 확인한다.
2. `deployment/news.env.example`을 서버의 `/etc/cosmos/news.env`로 복사해 서버별 값으로 설정한다. 이 파일은 일반 환경 변수 설정이며 저장소에 실제 비밀값을 넣지 않는다. systemd의 `WorkingDirectory`, `ExecStart`, `ReadWritePaths`는 예제 경로와 같아야 한다. 경로를 바꾸면 unit의 이 세 항목도 함께 바꾼다.
3. 기존 운영의 `state`와 `writer-state`는 보존하고 백업한다. 새 상태로 기존 HDFS 데이터를 이어 수집할 때는 아래 URL 색인을 **전체 Collector 시작 전에** 완료한다.
4. Writer부터 실행하고, 색인 완료를 확인한 뒤 Collector를 시작한다. 서비스 설치·시작은 운영자가 명시적으로 수행한다. 원래 운영 서비스와 새 서비스를 동시에 실행하지 않는다.

초기 색인은 HDFS의 URL 열만 읽으며 본문을 재전송하지 않는다. 필요한 Hadoop 환경 변수를 지정한 셸에서 실행한다.

```bash
export NEWS_APP_ROOT=/home/ubuntu/news-kafka/app
export NEWS_PYTHON=/home/ubuntu/news-kafka/venv/bin/python
export HDFS_HOST=100.117.115.44 HDFS_PORT=9000
"$NEWS_APP_ROOT/deployment/run-news-python.sh" -m services.news_pipeline.seed_existing \
  --state-dir /home/ubuntu/news-kafka/state
```

기본 색인 입력은 `/datasets/news/live/mysql_overseas/data`, `/datasets/news/snapshots/20260908-prepared/news`다. 다른 보존 입력은 `--root`를 반복해서 지정한다. `seed-status.json`의 `state=complete`, `limited=false`, 오류·미해결 행 0을 확인한다. `--max-files` 제한 실행 성공은 전체 완료가 아니다. 기본 Collector는 seed 완료 여부를 자동 검사하지 않으므로 이 선행조건을 생략하면 안 된다. 상태 DB를 그대로 보존하는 코드 재배포는 전체 색인을 다시 만들 필요가 없다.

두 unit 예제는 `/etc/cosmos/news.env`를 읽는다. 기존 상태 디렉터리와 service user의 소유권·권한을 확인한 뒤 적용한다. 세부 시작·재개·상태 확인은 [운영 문서](docs/OPERATIONS.md)를 참고한다.

## 내구성과 복구

후보와 커서, outbox는 SQLite에 영속 저장한다. Kafka ACK 후 전송 완료를 표시하고, HDFS에서는 파일 크기·해시 검증 후 디렉터리 rename으로 배치를 게시한 다음 offset을 commit한다. 재시작하면 HDFS manifest의 연속 offset을 복구한다.

생산자의 ACK 직후 SQLite 갱신 전에 중단되면 동일 이벤트가 다른 Kafka offset으로 다시 전송될 수 있다. 전체 흐름은 at-least-once이며 분석 단계의 `event_id` 중복 제거가 필요하다. 동일 URL의 후속 본문 수정은 자동 재수집하지 않는다. outbox는 전송 완료 본문도 보존하므로 장기 운영에는 별도 보존 정책이 필요하다.

Kafka 보존기간이 지나 아직 HDFS에 저장하지 못한 offset이 사라지면 Writer는 누락을 건너뛰지 않고 중단한다. 상태·토픽·HDFS 데이터를 삭제해 강제로 맞추지 말고 보존본을 확인해 복구한다. 큐·색인·outbox는 운영 상태이므로 Git에 넣거나 임의 삭제하지 않는다.

실제 Kafka/HDFS 재시작 검증용 `scripts/verify_news_kafka_hdfs.py`도 포함했다. 이 명령은 검증 전용 토픽·HDFS 데이터를 생성하므로 기본 테스트에 포함하지 않으며 운영 환경에서 의도적으로 실행한다. [출처와 이식 내역](docs/PROVENANCE.md), [Yahoo vendor GPLv3](vendor/overseas-news-crawler/LICENSE)도 함께 확인한다.

# 이식 범위와 의존성

2026-09-15에 Hadoop Master에서 운영하던 뉴스 수집·Kafka·HDFS 코드의 소스를 독립 실행 가능한 폴더로 정리했다. 기존 AWS ECS/RDS, MySQL 웹 API 및 웹 프런트엔드를 실행하지 않는다.

| 원래 작업 공간의 경로 | 이 폴더 안의 경로 | 용도 |
|---|---|---|
| `services/news_pipeline/*.py` | 동일 상대 경로 | 영속 큐, outbox, 스케줄러, Yahoo 어댑터, URL 색인, HDFS Writer |
| `services/news_pipeline/domestic_discover.mjs` | 동일 상대 경로 | 국내 목록 탐색·본문 추출 진입점 |
| `lib/news-article-extractor.ts` | 동일 상대 경로 | JSON-LD·매체별 규칙·Readability 본문 추출 |
| `lib/robots-policy.ts`, `lib/public-http-url.ts` | 동일 상대 경로 | robots 정책과 공개 HTTP URL 검사 |
| `overseas-news-crawler/lib/...` | `vendor/overseas-news-crawler/lib/...` | Yahoo HTTP·파서·SQLite 큐의 최소 모듈 5개 |
| `tests/test_news_kafka_*.py` | 동일 상대 경로 | 기존 Python 단위·복구 테스트 39개 |
| 뉴스 관련 Node 테스트 3개 | `tests/*.test.mjs` | 국내 목록·본문·robots 테스트 22개 |
| `scripts/verify_news_kafka_hdfs.py` | 동일 상대 경로 | 별도 검증 토픽을 만드는 선택적 통합 검증 |

국내 JavaScript 의존성은 `domestic_discover.mjs` → 위 TypeScript 3개 → `@mozilla/readability`와 `linkedom`에서 닫힌다. `esbuild`가 이를 Node용 단일 ESM으로 생성한다. 저장소에는 생성된 `domestic.bundle.mjs`를 올리지 않는다. Python vendor는 BeautifulSoup와 curl-cffi(없으면 requests), 표준 라이브러리를 사용하고, 새 Kafka·HDFS 코드에는 confluent-kafka와 PyArrow가 필요하다. Python 직접 의존성 버전은 기존 운영 요구사항과 동일하게 고정했다. Node 버전은 원래 작업 공간의 lockfile에서 확정한 버전으로 고정하고 이 모듈 전용 lockfile을 생성했다.

이식 과정에서 동작을 바꾸지 않고 배치·큐·재시도·robots 정책을 보존했다. 경로와 연결 설정은 다음처럼 조정했다.

- Collector의 vendor 기본 경로를 이 폴더에 상대적인 경로로 지정했다. `--crawler-root`로 덮어쓸 수 있다.
- Collector/Writer의 Kafka 기본 주소는 `KAFKA_BOOTSTRAP_SERVERS`, 미지정 시 `127.0.0.1:9092`다. 운영 주소는 예제 env에 명시한다.
- Seeder는 `HDFS_HOST`, `HDFS_PORT` 또는 Hadoop 기본 설정을 사용한다. Seeder/Writer의 HDFS 사용자는 `HADOOP_USER_NAME`으로 지정한다. 미지정 시 기존 `ubuntu`를 유지한다. Writer 복제 수 2는 그대로다.
- 배포 래퍼와 systemd 예제는 `/etc/cosmos/news.env`에 명시한 경로를 사용하도록 구성했다. 실제 운영 서버의 unit은 이번 소스 정리로 변경하지 않았다.
- 기존 통합 검증 스크립트는 Hadoop/Kafka가 있는 Linux에서 수동 실행해야 하며 기본 단위 테스트에 포함되지 않는다. 검증 전용 토픽과 HDFS 디렉터리를 실제 생성한다.

Yahoo vendor의 upstream·GPLv3 표시는 [NOTICE](../vendor/overseas-news-crawler/NOTICE.md)와 [LICENSE](../vendor/overseas-news-crawler/LICENSE)를 참고한다. vendor 원본 모듈은 수정하지 않았다.

운영 데이터, 실제 환경 파일·인증정보, SQLite 상태, 수집 본문, HDFS 감사 결과, node_modules, venv와 생성된 번들·배포 tar는 저장소에 포함하지 않는다.

# COSMOS 크롤러

국내·해외 뉴스와 국내·해외 공시 수집 코드를 이 폴더에서 관리한다. 뉴스는 공통 Kafka/HDFS 파이프라인으로 묶고, 수집 방식과 상태 관리가 다른 DART·SEC는 독립 실행 모듈로 구분했다.

| 폴더 | 포함 기능 | 실행 안내 |
|---|---|---|
| `news/` | 국내 뉴스·Yahoo Finance·네이버 API 코스피 100종목 수집, URL 중복 색인, Kafka 전달, HDFS Parquet Writer | [뉴스 README](news/README.md) |
| `prices/` | 코스피·나스닥 각 100종목 최대 기간 일봉 수집, 차트 JSON·Parquet, 장 마감 후 HDFS 게시·PostgreSQL 적재 | [주가 README](prices/README.md) |
| `disclosures/dart/` | OpenDART 수집·재개, 원본 ZIP 처리, HDFS 스냅샷 생성·검증·업로드 | [DART README](disclosures/dart/README.md) |
| `disclosures/sec/` | Nasdaq-100 SEC EDGAR 배치·증분 수집, 5분 주기 재조회, HDFS 업로드·검증 | [SEC README](disclosures/sec/README.md) |

각 모듈의 README를 기준으로 해당 폴더에서 의존성을 설치하고 실행한다. 웹 FrontEnd/BackEnd의 패키지 설치나 이전 AWS RDS 연결을 공통 실행 조건으로 삼지 않는다. HDFS 기능은 별도로 Hadoop 클라이언트·Java와 클러스터 접근 설정이 필요하다.

## 데이터 흐름

```text
국내 뉴스 / Yahoo Finance / 네이버 API
  → 로컬 영속 큐·outbox
  → Kafka news.raw
  → HDFS Parquet

OpenDART / SEC EDGAR
  → 수집 대상·진행 상태·원본 보존
  → 스냅샷 및 검증 정보
  → HDFS
```

공시 수집기는 현재 뉴스 Kafka 파이프라인과 별도다. 뉴스·공시의 원본 저장 이후 AI 분석, Spark 집계, PostgreSQL RDB Loader는 이 폴더의 기능에 포함하지 않는다.

## 운영 상태와 적용 범위

2026-09-15 점검 기준:

- 뉴스 수집기와 HDFS Writer는 Hadoop Master에서 동작 중이며 국내·해외 추가 적재를 확인했다.
- 네이버 API 수집기 2개도 별도 운영 중이다. 16:03 KST에 100종목 실제 조회와 본문 147건의 Kafka 전달을 확인했고, 실제 HDFS Parquet 적재를 검증했다. 기업별 기본 10분 주기이며 [상세 검증·한계](news/docs/NAVER.md)를 참고한다.
- 뉴스는 최신 목록과 미수집 기사·과거 기간을 순환한다. 모든 속보를 발행 즉시 수집하는 방식은 아니다. HelloT는 기존 기사 번호 범위 수집이며 일부 `article_not_found` 재시도가 있다.
- DART는 기존 키를 사용하는 별도 일별 수집기와 매시간 timer를 Master에 설치했다. 최초 실검증에서 2026-09-15 공시 11건을 HDFS에 저장하고 전체 파일 크기·SHA-256을 검증했다. 원천 미제공·빈 본문은 pending으로 재시도한다.
- DART 과거 배치에서 HDFS에 미반영된 13,741건을 recovery delta로 업로드하고 30개 파일 전체 크기·SHA-256을 검증했다(2026-09-15 14:00 KST). 원천 ZIP 손상과 기존 baseline의 빈 XML 1건은 보존하고 품질 예외로 기록했다.
- SEC는 운영자 연락처를 비공개 설정에 적용한 뒤 2026-09-15 14:35 KST에 증분 수집과 timer를 활성화했다. 기존 101개 CIK의 주요 공시를 조회하며, 이전 실행 종료 5분 뒤 다시 실행한다. 최초 실검증(14:37 KST)에서 신규 26건 중 1건의 HDFS 게시·SHA-256 검증을 확인했고 나머지 25건은 당시 수집 중이었다. 초기 적체와 실행 시간이 있어 발행 즉시 반영을 보장하지 않는다.

뉴스의 기존 운영 서비스를 유지하면서 공시 주기 수집을 별도 service/timer로 관리한다. 코드 release와 큐·상태·키를 다른 경로에 두며 실제 적용 시 각 모듈의 재개·중복 처리 절차를 따른다. 최신 공시 실행·복구 절차는 [DART 운영 문서](disclosures/dart/docs/daily-polling.md)와 [SEC 운영 문서](disclosures/sec/README.md)를 참고한다.

## 설정과 저장 데이터

- 인증정보는 환경변수 또는 서버의 별도 파일로 제공한다. `.env.example`에는 형식만 기록한다.
- SEC User-Agent의 연락처는 실행자가 실제 값으로 설정한다.
- 수집 원문, HDFS 스냅샷, SQLite 큐, 체크포인트, 로그, 인증서와 개인키는 Git에 포함하지 않는다.
- 뉴스의 생성된 JavaScript bundle과 배포 압축 파일도 Git에 넣지 않고 소스와 잠금 파일로 재생성한다.
- 수집 코드의 오프라인 테스트와 실제 원천 호출·HDFS 쓰기를 구분한다. 테스트 명령과 검증 범위는 각 README에 있다.

## 소스 출처

기존 `services/news_pipeline`, 국내 뉴스 추출 라이브러리, Yahoo 수집 모듈, DART·SEC 수집/업로드 스크립트를 필요한 의존성과 함께 분리했다. 출처가 있는 vendor 모듈은 해당 LICENSE와 출처 안내를 보존한다. 서로 다른 수집기의 CLI와 상태 파일 형식을 억지로 합치지 않아 기존 수집 재개 절차를 유지할 수 있다.

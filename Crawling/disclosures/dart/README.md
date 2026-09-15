# 국내 공시 OpenDART 수집기

기존 COSMOS 수집 코드에서 국내 공시 수집·중단 재개·본문 파싱·HDFS 저장에 필요한 부분을 독립 모듈로 모았다. **공시 코드는 스냅샷을 HDFS에 직접 올린다. Kafka 연결과 RDB Loader는 포함하지 않는다.**

Python 3.11 이상을 사용한다. Python 실행·테스트는 표준 라이브러리만 필요하다. 유니버스/정기보고서 수집용 Node.js 도구를 사용할 때만 Node.js 22.13 이상과 `npm ci`가 필요하다. 아래 명령의 작업 디렉터리는 모두 이 `dart` 폴더다.

## 구성

| 파일 | 역할 |
|---|---|
| `scripts/collect_dart_rank_range.py` | 고정 순위·기간의 공시 목록과 원본문서 ZIP 수집, 파싱, 재개 |
| `scripts/dart_documents.py` | ZIP의 XML/HTML/TXT 본문·표 파싱; 외부 엔티티를 읽지 않음 |
| `scripts/collect-opendart-kospi-top100.mjs` | 유니버스 생성 및 기존 2025 연간·2026 분기/반기 수집 |
| `scripts/run_dart_hdfs_service.py` | 51–100위 3개 shard 관리, 할당량 대기·재시도·최종 HDFS 적재 |
| `scripts/run_dart_daily.py` | 1–100위 최신 공시 날짜별 재조회·미게시 본문만 HDFS delta 적재 |
| `scripts/prepare_dart_hdfs_snapshot.py` | 완전한 본문 행과 대응 원본 ZIP을 고정 스냅샷으로 패키징 |
| `scripts/upload_dart_hdfs_snapshot.py` | 파일 크기·SHA-256 검증 후 HDFS 임시 경로를 최종 경로로 확정 |
| `scripts/scan_dart_zip_credentials.py`, `scripts/import_dart_zip_hdfs.py` | 사용자 제공 CSV·본문 JSONL ZIP 검사 및 HDFS import |
| `scripts/validate_dart_rank_output.py` | 로컬 수집 결과의 읽기 전용 품질 점검 |
| `config/universe-20260907.jsonl` | 2026-09-07 고정 순위 100개사의 공개 회사 식별자 |

설정 파일에는 회사 식별자·순위·기준일만 있다. 공시 본문, 주가 수집값, 실제 키, 수집 결과, 호출량 장부와 운영 상태는 포함하지 않았다.

## 공시 목록·본문 수집

`.env.example`을 `.env.dart.local`로 복사하고 `DART_API_KEY`에 자신의 키를 입력한다. Linux에서는 키 파일 권한을 `600`으로 제한한다. API 키를 CLI 인자로 전달하지 않는다. 단일 수집기는 `DART_API_KEY` 환경 변수도 지원한다.

```bash
python scripts/collect_dart_rank_range.py \
  --rank-from 51 --rank-to 100 --begin 20160101 --end 20260909 \
  --output output/rank51-100-20160101-20260909 \
  --key-file .env.dart.local --workers 4 --interval 0.3

python scripts/validate_dart_rank_output.py \
  output/rank51-100-20160101-20260909 --key-file .env.dart.local
```

같은 명령·같은 출력 경로를 재실행하면 목록 캐시와 수집 상태를 이어받는다. 기존 출력의 순위·날짜 범위가 다르면 거부한다. `--plan-only`는 목록만 수집한다. `--reuse-source <기존 출력>`을 반복해서 전달하면 별도 캐시를 재사용한다. 기본값으로 다른 프로젝트의 출력 경로를 읽지 않는다.

임원·주요주주 특정증권 소유상황(D002)과 거래계획(D005)은 제외하며 정정 공시는 유지한다. 일일 요청 수는 동일 키의 로컬 장부를 공유하지만 다른 서버에서 사용하는 요청까지 알 수는 없다.

`--universe`로 같은 스키마의 별도 JSONL을 지정할 수 있다. 묶인 유니버스는 **현재 시총 순위가 아닌 2026-09-07의 고정 표본**이다. 신규 유니버스를 만들려면 다음 명령을 사용하고 생성된 `normalized/universe.jsonl`을 명시적으로 지정한다.

```bash
npm ci
npm run collect:universe -- --as-of 2026-09-15 --output output/universe-20260915
```

유니버스 생성에도 `DART_API_KEY`가 필요하다. `corpCode.xml`의 DART 회사코드를 연결한 뒤 JSONL을 저장하므로 Python 수집기의 `--universe` 입력으로 사용할 수 있다. `--as-of`는 캡처의 기준일 표시이며 과거 시장 순위를 복원하지 않는다. `npm run collect:periodic`은 기존 코드에 고정된 2025 사업보고서·2026 1분기·반기 및 관련 API를 수집한다. 다른 보고연도에는 `REPORT_PERIODS`와 조회 기간 계약을 먼저 갱신해야 한다.

## 서버 재개와 HDFS

최신 공시를 지속 수집하려면 [일별 주기 수집](docs/daily-polling.md)의 별도 runner와 hourly timer를 사용한다. 최근 3일 목록을 재조회하고 정상 본문만 검증 게시하며, 개별 오류/일시적 원천 미제공은 다음 pass에서 재시도한다. 과거 수집 출력과 다른 output을 사용한다.

[HDFS 실행 절차](docs/hdfs.md)를 따른다. 서버 재개기는 기존 운영과 같은 **51–100위, 2016-01-01 시작, 3개 shard**를 대상으로 한다. `--end`를 명시하고 동일 값으로 재개한다. 임의 순위 범위는 단일 수집기 CLI를 사용한다.

2026-09-15에 기존 운영 서비스의 `needs_attention / retries_exhausted` 원인을 확인했다. 추가 원천 ZIP 손상은 재다운로드한 바이트와 SHA-256까지 일치할 때만 예외로 인정하고 원본을 보존한다. 과거 HDFS index와의 차집합 복구에는 `prepare_dart_recovery_delta.py`를 사용한다. `needs_attention` 상태를 자동 삭제하지 않으며 업로드 검증 보고서와 원본을 대조해 상태를 정리한다.

최신 공시용 `cosmos-dart-daily.timer`는 Master에서 매시간 실행하도록 설치했다. 최초 실검증에서 2026-09-15 공시 11건이 HDFS 크기·SHA-256 검증까지 통과했다. 이 날짜를 재조회해 새 목록이 반영되고 기존 11건이 중복 게시되지 않는 것도 확인했다. 원천 미제공 문서는 pending으로 남기므로 timer 활성화가 모든 공시 본문의 확보 완료를 뜻하지 않는다.

이어서 9월 14일 공시 40건의 게시도 검증했다. 2016-01-01~2026-09-09의 기존 51–100위 배치에서 HDFS에 빠져 있던 13,741건은 `/datasets/opendart/snapshots/20260915-recovered-rank51-100-delta`에 복구했다. 2026-09-15 14:00 KST 기준 30개 파일(1,079,950,235 bytes)의 크기·SHA-256 검증이 완료됐다. 과거 baseline은 그대로 유지하며 baseline에 이미 있던 빈 XML 1건도 품질 예외로 보존한다. 원천 손상 4건을 정상 본문 수집으로 계산하지 않는다.

## 검증

```bash
python -m unittest discover -s tests -p "test_*.py"
npm ci
npm test
```

테스트는 임시 fixture와 가짜 API/HDFS 객체를 사용하며 실제 API 호출·HDFS 업로드·운영 데이터 변경을 하지 않는다. 파서의 표·인코딩 처리, 호출 제한, 중단 후 재개, ZIP 경로 안전성, 누락/손상 파일, checksum, 업로드 재개 등을 검증한다.

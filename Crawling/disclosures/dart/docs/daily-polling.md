# 최신 공시 주기 수집

`scripts/run_dart_daily.py`는 한 번의 제한된 수집을 수행하고 종료한다. `deploy/cosmos-dart-daily.service.example`과 `.timer.example`은 매시간 다시 실행하는 예시다. 설치만으로 활성화되지 않는다. 실제 배포 시 운영 계정·키 파일·기존 호출량 장부·Hadoop 설정 경로를 지정한다.

```bash
python scripts/run_dart_daily.py \
  --output /var/lib/cosmos-dart-daily \
  --key-file /private/path/opendart.env \
  --ledger-dir /path/to/existing/opendart-rank-request-ledgers \
  --start-date 20260910 --overlap-days 3 --max-days-per-run 7 \
  --interval 0.5 --request-budget 2500
```

## 수집 범위와 재조회

- 기본 입력은 기존 2026-09-07 고정 표본 1–100위다. 현재 시총 순위를 자동 교체하지 않는다.
- 날짜는 KST 접수일 기준이다. 매 pass마다 오늘·어제·그제 목록을 새 경로에서 조회하여 DART의 당일 추가/지연 등록을 반영한다. 같은 날짜의 완료 목록 캐시를 재사용하지 않는다.
- 최근 3일 다음에는 `--start-date` 이후 아직 조회하지 않았거나 오류/원천 미제공이 남은 과거 일자를 재시도한다. 오래 확인하지 않은 일자부터 `--max-days-per-run` 한도 안에서 처리한다.
- 최초 운영 점검은 `--max-days-per-run 1`로 오늘분만 실행할 수 있다. 검증 후 같은 output에서 5 또는 7로 늘린다. 1로 계속 두면 overlap·과거 보완 일자는 처리되지 않는다.
- 최근 3일보다 오래되었고 미해결 공시가 없는 확인 완료 일자는 확정 처리한다. 3일을 넘는 지연 등록까지 매번 탐지하는 방식은 아니므로 필요하면 overlap을 늘리거나 별도 기간 점검을 수행한다.
- 기존 과거 수집은 별도 경로 그대로 유지한다. 이미 과거 배치가 소유한 기간과 겹치지 않게 최초 시작일을 정한다. 이 runner의 중복 제거는 자신의 `daily-dart-v1-*` 스냅샷 범위다.

## 적재와 중복 방지

100개사 목록을 모두 확인한 뒤 아직 HDFS에서 검증되지 않은 접수번호만 본문을 받는다. D002/D005 제외와 정정 공시 보존은 기존 수집기와 같다. 성공한 본문을 기존 `prepare()`와 `upload()`로 ZIP 보존·gzip JSONL·문서 index·checksum manifest에 패키징한다.

HDFS 경로는 `/datasets/opendart/snapshots/daily-dart-v1-<universe hash>-<YYYYMMDD>-<uuid>`다. 각 스냅샷은 그 날짜의 **새 공시만 담은 delta**다. 같은 날짜를 읽을 때는 delta들의 합집합을 사용한다. 과거 대량 저장본과 raw ZIP/TAR를 중복 합산하지 않는다.

업로드 예정 경로와 접수번호를 로컬 `checkpoint.json`의 `pending_upload`에 먼저 기록한다. 최종 HDFS 파일 크기·SHA-256 검증이 통과한 뒤에만 `published` 접수번호를 갱신한다. 업로드 후 프로세스가 중단되면 다음 pass가 같은 경로를 검증하므로 새로운 복제 스냅샷을 만들지 않는다. 같은 접수번호는 재다운로드·재적재하지 않는다.

`raw-cache.json`은 아직 게시되지 않은 수집 중간 원본의 경로·SHA-256을 보존한다. 재시도 때 해시가 일치하는 원본만 다시 파싱해서 재사용하며, 달라졌으면 다시 요청한다. 임시 `013/014` 원천 미제공 상태는 영구 제외하지 않고 다음 pass에서 다시 요청한다. 개별 본문/파싱 오류는 접수번호별 pending으로 남기고 그날 정상 본문은 먼저 게시한다. 인증·할당량·네트워크 오류는 이미 확보한 정상 본문을 검증 게시한 뒤 pass를 중단한다. 목록 단계가 불완전하면 해당 일자는 게시하지 않는다.

공시가 없거나 새 본문이 없는 일자는 목록 확인 상태만 기록하고 가짜 빈 본문 스냅샷을 만들지 않는다. `status=idle`은 이번 pass가 끝났다는 뜻이며 원천 미제공 문서까지 모두 확보했다는 뜻이 아니다. `days`의 `pending_count`, `failures`, `stop_reason`도 확인한다.

## 복원과 운영 조건

출력 경로와 기존 shared ledger를 release 디렉터리 밖에 보존한다. 같은 output은 OS 파일 잠금으로 중복 실행을 막는다. 여러 호스트에서 동일 작업을 병렬로 운영하는 용도는 아니다. API 키는 `--key-file`만 읽으며 부모 환경의 다른 `DART_API_KEY`를 무시한다.

`--ledger-dir`는 과거 수집기의 **정확히 같은 실제 디렉터리**여야 한다. 다른 경로를 지정하면 동일 키의 요청을 합산할 수 없다. 키별 잠금과 19,500회 일일 기본 한도, pass당 요청 한도를 함께 사용한다. 0.5초 요청 간격·매시간 최근 3일 조회는 기본 목록만 약 7,200회/일이며 페이지 수·본문·과거 backfill이 추가된다. 한도에 도달하면 pass를 종료하고 다음 timer에서 장부를 다시 확인한다.

`checkpoint.json`이 없으면 같은 유니버스 해시 prefix의 최종 HDFS 스냅샷만 찾아 모든 파일 해시와 index 건수를 검증하고 `published`를 재구성한다. 과거 비어 있던 조회 일자 상태는 복원되지 않아 다시 조회하지만 저장된 공시는 중복 게시하지 않는다. 손상된 HDFS 스냅샷은 묵인하지 않고 복원을 중단한다. 로컬 `pending_upload`가 남은 상태에서는 그 로컬 패키지를 보존해야 한다.

오류 pass는 종료코드 `75`이고 다음 timer에 다시 시도한다. 자동으로 키를 바꾸거나 운영 과거 파일을 지우지 않는다. `attempts/`는 감사·복구를 위해 보존하므로 디스크 사용량을 확인하고 verified 스냅샷·복구 필요 상태를 검토한 뒤 별도 보존 정책을 적용한다.

# 해외 공시: SEC EDGAR 배치·증분 수집기

Nasdaq의 현재 Nasdaq-100 구성증권을 SEC CIK와 연결한 뒤 기업정보와 공시 원문을 받는다.
같은 회사의 복수 주식종류는 CIK 기준으로 통합한다. `2016~2026` 같은 기간을 지정해도
**실행 시점의 구성기업에 대한 과거 공시**를 수집한다. 각 연도의 당시 구성종목을 복원하는
프로그램은 아니다.

기존 운영 이력은 해외 공시 **16,133개 원문 파일의 배치 수집·HDFS 스냅샷 적재 완료**다.
기존 배치 수집기와 함께 `run_sec_incremental.py`·systemd timer 예제를 제공한다. 증분 수집은
기존 배치의 고정 기업 표본에서 새 공시를 주기적으로 찾아 HDFS에 직접 저장한다. 운영
서버에서는 2026-09-15 14:35 KST에 연락처 설정·SEC 실제 응답 확인 후 증분 service/timer를
활성화했다. 최초 검증(14:37 KST)에서 신규 26건 중 1건의 HDFS 게시 및 저장 파일의 크기·
SHA-256 재검증을 완료했고 나머지 25건은 당시 수집 중이었다. 이 기록은 해당 시점의 운영
검증이며, 새 서버에는 아래 연락처·표본·HDFS 설정과 service/timer 설치가 필요하다.
Kafka producer는 포함하지 않는다. 데이터·완료 manifest·접속키도 이 디렉터리에 포함하지 않는다.

## 실행 환경

- Python **3.11 이상**. 수집·재개·테스트는 표준 라이브러리만 사용한다.
- 증분 runner는 파일 잠금·HDFS 스트림 제한을 위해 Linux에서 실행한다. 오프라인 테스트는 Windows도 지원한다.
- 수집 시 Nasdaq와 SEC HTTPS 접근이 필요하다.
- HDFS 업로드·검증은 Hadoop client가 설정된 Linux에서 Bash, GNU coreutils, Java와 함께 실행한다.
- 아래 명령은 저장소의 `Crawling/disclosures/sec` 디렉터리에서 실행한다.

`requirements.txt`에는 설치할 외부 Python 패키지가 없다.

```bash
python3 --version
python3 scripts/collect_sec_edgar_nasdaq100.py --help
python3 scripts/resume_sec_edgar_from_manifest.py --help
```

## 환경변수

`.env.example`은 형식 예시다. 프로그램은 `.env` 파일을 자동으로 읽지 않는다.
실행 환경에서 `SEC_EDGAR_USER_AGENT`에 서비스명과 운영자의 실제 연락 이메일을 설정한다.
예시 이메일 `operator@example.com`은 그대로 실행하지 못하도록 검증한다.

```bash
export SEC_EDGAR_USER_AGENT='YOUR_APPLICATION/1.0 YOUR_REAL_CONTACT_EMAIL'
```

PowerShell에서는 `$env:SEC_EDGAR_USER_AGENT = 'YOUR_APPLICATION/1.0 YOUR_REAL_CONTACT_EMAIL'`을 사용한다.
실제 연락처나 비밀 파일은 Git에 추가하지 않는다.

## 새 배치 수집

기본 실행은 기업별 최신 핵심 공시 후보를 선택한 후 전체 최신순 최대 100건을 받는다.
기본 양식은 `10-K`, `10-Q`, `8-K`, `20-F`, `40-F`, `6-K`이며 수정신고서(`/A`)도 포함한다.

```bash
python3 scripts/collect_sec_edgar_nasdaq100.py \
  --out-dir output/sec-edgar-latest
```

기간 전체를 수집하려면 두 제한을 모두 `0`으로 지정한다. 아래 날짜는 예시이므로 원하는
시점으로 바꾼다. `--metadata-only`는 먼저 대상 목록을 확인할 때 사용한다.

```bash
python3 scripts/collect_sec_edgar_nasdaq100.py \
  --start-date 2016-01-01 --end-date 2026-09-15 \
  --per-company 0 --total-limit 0 --metadata-only \
  --out-dir output/sec-edgar-preflight

python3 scripts/collect_sec_edgar_nasdaq100.py \
  --start-date 2016-01-01 --end-date 2026-09-15 \
  --per-company 0 --total-limit 0 \
  --out-dir output/sec-edgar-batch
```

모든 신고 양식이 필요하면 `--forms all`을 추가한다. 기본 요청 간격은 0.25초이며
`--request-delay`는 0.1초 이상이어야 한다. 다운로드 작업 4개가 하나의 속도 제한을 공유한다.
이 제한은 한 프로세스 안에서 적용되므로 여러 수집기를 동시에 실행할 때는 총 요청량을
별도로 관리해야 한다.

출력 경로는 `--out-dir`로 지정하며 상대경로는 현재 작업 디렉터리 기준이다.

```text
output/sec-edgar-batch/
├── README.md
├── companies.json
├── selected_filings.jsonl
├── filings.jsonl
├── failures.jsonl
├── manifest.json
└── raw/<기업순번_티커>/<접수일_양식_ACCESSION>.txt
```

`filings.jsonl`에는 확보한 원문의 상대경로·바이트 크기·SHA-256이 들어간다.
기본 수집기는 기존 파일이 100바이트 이상이면 재사용하므로, 엄격한 무결성 검사가 필요하면
아래 manifest 기반 재개를 사용한다. 실행 중단 직전의 일부 파일은 메모리 내 목록만 갱신된
상태일 수 있으므로 같은 수집 명령 재실행과 manifest 재개의 역할을 구분한다.

## 기존 manifest에서 원문 복구·검증

기존 `filings.jsonl`과 기업정보·선택 목록 등 배치 메타데이터를 별도로 복원한 폴더를 지정한다.
재개기는 manifest에 있는 파일의 크기와 SHA-256을 확인하고 없거나 손상된 파일만 다시 받는다.
`selected_filings.jsonl`만으로는 재개할 수 없으며, manifest에 없는 새 공시를 발견하지 않는다.

```bash
python3 scripts/resume_sec_edgar_from_manifest.py \
  --root output/sec-edgar-batch --workers 4 --request-delay 0.2

# 같은 작업의 Linux wrapper. 임의의 현재 작업 디렉터리에서도 실행 가능하다.
bash scripts/run_sec_edgar_resume_remote.sh output/sec-edgar-batch --workers 4
```

wrapper는 `SEC_EDGAR_USER_AGENT` 대신 `SEC_EDGAR_USER_AGENT_FILE`로 지정한 연락처 파일을
읽을 수도 있다. 이 파일은 자동 삭제하지 않는다. 결과는 `resume-report.json`에 남으며
실패가 있으면 종료 코드 `1`을 반환한다.

## HDFS 스냅샷 업로드·검증

Hadoop client가 기존 클러스터를 가리키도록 `HADOOP_CONF_DIR`·`JAVA_HOME`을 설정한다.
`HDFS_BIN`을 생략하면 `${HADOOP_HOME:-/opt/hadoop}/bin/hdfs`를 사용한다. 서버 주소, SSH 키,
사용자 홈 경로, 특정 스냅샷 이름이나 파일 개수는 코드에 고정하지 않았다.

```bash
export HDFS_BIN=/opt/hadoop/bin/hdfs
export HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop
export JAVA_HOME=/path/to/your/jdk

bash scripts/load_sec_edgar_hdfs_remote.sh \
  output/sec-edgar-batch /datasets/sec-edgar/snapshots/YOUR_SNAPSHOT.inprogress
bash scripts/verify_sec_edgar_hdfs_remote.sh \
  output/sec-edgar-batch /datasets/sec-edgar/snapshots/YOUR_SNAPSHOT.inprogress
```

업로드는 기존 대상 경로를 덮어쓰지 않고, 수집 결과 외 파일이나 심볼릭 링크가 있으면 거부한다.
파일·바이트 개수는 해당 로컬 배치에서 계산한다. 검증은 경로별 크기, 메타데이터 SHA-256,
전체 원문을 연결한 SHA-256, HDFS `fsck`를 확인한다. 전체 원문을 다시 읽으므로 데이터
크기에 따라 시간이 걸린다. 검증 중에는 로컬 배치와 HDFS 스냅샷을 수정하지 않는다.

두 보조 스크립트는 `_SUCCESS` 생성, 완료 경로로 이름 변경, 기존 데이터 삭제를 자동 수행하지
않는다. 검증이 성공한 배치의 공개는 운영 절차에 따라 별도로 진행한다. `_SUCCESS`가 있는
완료 스냅샷을 검증할 때도 같은 검증 스크립트를 사용할 수 있다.

## 기존 표본으로 최신 공시 주기 수집

`scripts/run_sec_incremental.py`는 한 번 실행한 뒤 종료하는 runner다. 기존 배치의
`companies.json`을 `--universe`로 지정한다. 현재 Nasdaq 구성종목을 새로 조회하지 않으며,
기존 2026-09-08 종료 배치의 **101개 CIK 표본과 핵심 공시 6종**을 그대로 사용할 수 있다.
기본 조회 범위는 **2026-09-09부터 실행 당일 UTC 날짜까지**, 요청 간격은 기존 0.2초다.
각 회차마다 범위 내 목록을 다시 조회하므로 같은 날짜에 나중에 접수된 공시도 발견한다.
이전 배치 파일과 2016년부터의 완료 스냅샷은 변경하지 않는다.

```bash
python3 scripts/run_sec_incremental.py \
  --state-dir /home/ubuntu/sec-edgar-incremental/state \
  --universe /home/ubuntu/sec-edgar-incremental/config/companies.json \
  --user-agent-file /etc/cosmos/sec-user-agent \
  --hdfs-root /data-lake/raw/realtime/sec-edgar
```

`--user-agent-file`은 실제 운영자가 제공한 서비스명·연락 이메일 한 줄을 담은 비공개 파일이다.
파일을 생략하면 `SEC_EDGAR_USER_AGENT` 환경변수를 사용한다. 실제 값이 없으면 실행하지
않으며 코드에 예시 연락처를 대체값으로 넣지 않는다. 원천이 HTTP 403을 반환하면 해당 회차는
`blocked_source_access`로 종료한다. 차단을 피하기 위한 다른 계정·프록시·쿠키 전환을 하지 않는다.

신규 저장 경로는 기존 배치 스냅샷과 분리되어 있다.

```text
/data-lake/raw/realtime/sec-edgar/
  filing_date=2026-09-15/cik=123/accession=0000000123-26-000001/
    raw.txt
    metadata.json
    _manifest.json
    _SUCCESS
```

위 경로는 구조 예시이며 테스트 원문을 운영 HDFS에 넣지 않는다. `raw.txt`는 SEC complete
submission 원문이다. 다운로드는 SEC 문서/헤더 및 접수번호를 확인한 뒤에만 저장한다.
로컬 spool은 원문·메타데이터·SHA-256 manifest를 fsync한 후 사용하고, HDFS에 작은 공시별
임시 디렉터리를 만든 뒤 파일별 크기·SHA-256·파일 개수를 검증한다. `_SUCCESS`를 포함한
검증 완료 디렉터리를 최종 접수번호 경로로 원자적으로 이동한 뒤 SQLite를 `published`로
갱신한다. 정상 게시가 확인된 이번 spool 파일만 제거하며 기존 배치 원문·상태는 삭제하지 않는다.

`CIK + accession number`가 영속 중복 키다. 파일 순위·티커·실행 날짜가 달라도 이미 게시한
접수번호를 다시 쓰지 않는다. HDFS rename 후 SQLite 갱신 전에 종료되면 다음 회차에 HDFS
완료 marker·manifest·해시를 검증하고 복구한다. 불완전한 HDFS staging은 누락 파일을 이어
올리며, 이미 있는 파일의 해시가 다르면 덮어써서 정상으로 만들지 않고 조사가 필요한 상태로
남긴다. 여러 서버에서 같은 HDFS 증분 경로에 동시에 쓰지 않는다.

`incremental.db`에는 표본 해시·시작일·양식·HDFS 경로 계약과 접수번호별 진행 상태가 남는다.
표본 또는 계약을 바꾸거나 상태 DB를 삭제해 새 실행처럼 만들지 않는다. 계약 변경은 기존
입력 범위와 게시 결과를 비교한 별도 이관 작업이 필요하다. 초기 적체는 `--max-filings`
(기본 200건)씩 처리하고, 남은 대상이 있으면 `pending_backlog`로 기록한다. 수집 오류는
`needs_attention`, 전체 발견 대상의 처리가 끝났으면 `complete`다. 기업 목록 조회 실패도
오류로 집계하므로 일부 기업만 확인한 실행을 전체 완료로 표시하지 않는다.

### timer 설치 예제

`deployment/sec.env.example`을 `/etc/cosmos/sec.env`로 복사하고 실제 프로그램·상태·표본·
연락처 파일 경로를 맞춘다. 기본 app 경로는 `/home/ubuntu/sec-edgar-incremental/app`이다.
같은 디렉터리의 service/timer를 `/etc/systemd/system/`에 설치하기 전에 `WorkingDirectory`,
`ExecStart`, `ReadWritePaths`, 사용자 및 파일 권한을 실제 서버와 맞춘다. 상태 디렉터리를 미리
만들고 service 사용자에게만 쓰기 권한을 준다. 연락처 파일 권한은 `600`으로 제한한다.

```bash
sudo systemctl daemon-reload
# 실제 설정·고정 표본·HDFS 접근을 준비한 뒤 첫 회차 결과를 검증한다.
sudo systemctl start cosmos-sec-incremental.service
sudo systemctl enable --now cosmos-sec-incremental.timer
systemctl list-timers cosmos-sec-incremental.timer
cat /home/ubuntu/sec-edgar-incremental/state/status.json
```

timer는 부팅 후 1분, 이후 **이전 실행 종료 5분 뒤** 다시 실행하며 최대 15초 무작위 지연이
있다. SEC 공개 API를 반복 조회하는 준실시간 수집이며, 발견 지연에는 조회·원문 다운로드·HDFS
검증 시간이 추가된다. 초기 적체가 있으면 먼저 처리해야 한다. 한 회차가 오래 걸리면 같은
서비스가 겹쳐 실행되지 않는다. 정상인 oneshot은 실행 후
`inactive (dead)`가 될 수 있으므로 timer의 다음 실행 시각·마지막 종료 코드·`status.json`의
조회 기업 수·게시/미처리 개수를 함께 확인한다. 서비스 시작만으로 최신 공시가 모두 들어왔다고
표시하지 않으며, 초기 적체와 실제 원천 응답은 운영 검증 결과로 확인한다.

## 오프라인 테스트

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash -n scripts/load_sec_edgar_hdfs_remote.sh
bash -n scripts/verify_sec_edgar_hdfs_remote.sh
bash -n scripts/run_sec_edgar_resume_remote.sh
bash -n deployment/run-incremental.sh
```

테스트는 합성 메타데이터와 임시 파일을 사용한다. 인터넷·실제 공시·HDFS·운영 인증정보에
접근하지 않는다. 테스트 통과가 SEC 접속이나 운영 HDFS 적재를 대신 검증하지는 않는다.
증분 테스트는 같은 날짜의 신규 접수, 반복 실행 중복 방지, 부분 업로드, 게시 응답 유실 복구,
로컬/HDFS 손상 거부, 차단 HTML·403 거부 및 변경된 상태 계약 거부를 포함한다.

공식 출처: [Nasdaq Global Index Watch](https://indexes.nasdaq.com/Index/Weighting/NDX),
[SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[SEC Developer Resources](https://www.sec.gov/about/developer-resources).

# 주가 일봉 → HDFS 일일 게시

한국 18:30, 미국 10:00 **Asia/Seoul 기준 하루 한 번** 확정 일봉을 수집하고 HDFS에 게시하는 템플릿이다. 장중 실시간 시세 수집기는 아니다. 운영 서버 등록은 별도 절차이며 파일을 배치하는 것만으로 스케줄이 시작되지 않는다.

## 처리 과정

1. 시장별 기본 100종목을 `collect --markets KOSPI|NASDAQ --max-history --refresh`로 재조회한다. 공급자가 제공하는 전체 과거 범위를 요청하고, 변경된 수정주가를 기존 과거 자료와 함께 다시 검증한다. 60초 이내의 성공 결과는 기존 수집기 캐시 규칙을 따른다.
2. 모든 선택 종목 수집이 성공한 `status=complete` 스냅샷만 다음 단계로 넘긴다. 일부 실패, 빈 가격, 회사·행 수 불일치 또는 원천 오류가 있으면 HDFS 게시를 중단한다. 수집 단계의 정상 기존 데이터는 유지된다.
3. 로컬 Parquet를 8,192행씩 읽어 OHLCV, 회사, 날짜 범위, 중복·순서, UTC 자정 표기와 manifest의 실제 행 수·기간을 검증한다. 격리 원본 JSONL의 해시·행 수·사유도 확인한다.
4. 고유 `.staging` 경로에 원본을 올리고 원격 파일 크기·SHA-256을 모두 확인한다. 로컬에서 전행 검증한 Parquet와 원격 바이트가 같으므로 원격 행 수도 동일함을 확인한다. 검증을 통과한 뒤 `_SUCCESS`를 추가하고 디렉터리를 원자적으로 최종 경로로 옮긴다.
5. PostgreSQL 적재는 `STOCK_PRICE_PG_LOAD=1`을 따로 설정했을 때만 HDFS 게시 후 수행한다. 기본값은 `0`이며 HDFS 게시 성공이 곧 PostgreSQL 적재를 의미하지 않는다.

한국은 18:00 KST 이후, 미국은 20:00 America/New_York 이후의 날짜만 수집 대상으로 인정한다. 미국 10:00 KST 실행은 서머타임·표준시 모두 전일 완료 기준을 충족한다. 주말·휴일에도 원천을 하루 한 번 확인하며 실제 마지막 거래일은 달라지지 않을 수 있다. `Persistent=true`는 서버가 꺼져 놓친 실행을 다음 기동 시 한 번 수행한다.

## HDFS 계약

```text
/data-lake/raw/stock-prices/daily/
  market=KOSPI/as_of=2026-09-15/snapshot=<run_id>/
    prices_daily.parquet
    manifest.json
    selection.json
    quarantine.jsonl
    publication.json
    _SUCCESS
  .staging/<run_id>-<attempt_uuid>/
  .locks/<market>-<as_of>-<run_id>
```

- 한 스냅샷은 한 시장만 포함한다. 두 시장이 섞인 기존 export는 게시하지 않고 시장별로 다시 export한다.
- 각 스냅샷에는 해당 시장의 수집된 전체 과거 이력이 들어 있다. 소비자는 시장별로 필요한 완료 스냅샷 하나를 선택해 읽는다.
  여러 기준일의 전체 스냅샷을 합치면 같은 일봉이 반복되므로, 최신 데이터를 처리할 때는 가장 최근의 완료 스냅샷을 사용한다.
- `as_of`는 manifest의 `requested_end`, 즉 요청한 완료 기준일이다. 실제 마지막 거래일이나 모든 종목의 최신일을 뜻하지 않는다.
- `publication.json`은 출처·가격 기준·라이브러리 버전·전체/종목별 실제 첫날과 마지막 날·정상/격리 행 수·원본 파일 크기와 SHA-256을 보존한다. 기존 manifest와 격리 원본의 내용은 변경하지 않는다.
- `_SUCCESS`는 `publication.json`의 SHA-256을 담는다. 소비자는 최종 `market=.../as_of=.../snapshot=...` 경로에서 `_SUCCESS`가 있는 스냅샷만 읽어야 한다. `.staging`을 재귀 스캔하지 않는다.
- 동일 `run_id`를 재실행하면 최종 경로의 모든 크기·해시를 비교해 `already_verified`로 끝난다. 기존 스냅샷과 내용이 다르면 오류로 종료한다. 기존 스냅샷을 덮어쓰거나 삭제하지 않는다.
- 게시 잠금은 HDFS `mkdir`로 독점 획득한다. 정상 종료·예외에서는 빈 잠금만 해제한다. 프로세스 강제 종료로 남은 잠금은 해당 실행이 끝났는지 운영자가 확인한 후 수동 해제한다. 자동 잠금 탈취는 하지 않는다.
- 실패한 staging은 조사할 수 있도록 남긴다. 재시도는 새 staging을 사용한다. 데이터·로컬 export·실패 staging의 보관 기간과 삭제는 자동화하지 않는다. 전체 과거 스냅샷을 매일 추가하므로 복제 계수를 포함해 용량을 산정해야 한다.

## 수동 검증과 게시

프로젝트의 `Crawling/prices` 디렉터리에서 실행한다. HDFS 자격 증명은 기존 Hadoop 설정/Kerberos 등 외부 환경을 사용하며 코드에 넣지 않는다.

```bash
# 로컬 검증과 경로 계획만 수행한다. 기본 동작에서는 HDFS에 접근하지 않는다.
python -m services.stock_prices.cli hdfs-publish --source /path/to/export --market KOSPI

# 실제 클러스터와 실행 파일을 외부 환경에서 지정한 후 명시적으로 게시한다.
export HDFS_URI=hdfs://namenode.example.internal:9000
export HDFS_BIN=/opt/hadoop/bin/hdfs
python -m services.stock_prices.cli hdfs-publish --source /path/to/export --market KOSPI --publish
```

출력 JSON의 `status`는 `dry_run`, `published`, `already_verified` 중 하나다. `source`, `destination`, `manifest_path`, `sha`, `rows`와 실제 데이터 범위를 함께 반환한다. 임의 shell 문자열이나 URI의 계정·암호는 허용하지 않는다.

## 운영 배치

필요 조건: Linux Bash, `flock`, Hadoop CLI/설정, Hadoop용 Java, `Crawling/prices/requirements.txt`를 설치한 Python 가상환경. 기본 서비스 사용자는 `ubuntu`이며 다른 계정이면 서비스 파일과 디렉터리 소유자를 함께 조정한다.

1. 저장소를 버전별 release에 배치하고 `/opt/cosmos/stock-prices/current`가 사용할 release를 가리키도록 한다. 실행 중인 release를 덮어쓰지 않는다.
2. `stock-prices.env.example`을 `/etc/cosmos/stock-prices.env`에 복사해 `0600`으로 관리한다. 실제 Python·프로젝트 경로·`HDFS_URI`·Hadoop 환경을 설정한다. 로컬 state/runs 경로를 실행 계정이 쓸 수 있어야 한다.
3. 초기 `STOCK_PRICE_PUBLISH=0`, `STOCK_PRICE_PG_LOAD=0` 상태에서 시장별 수동 실행과 로컬 검증을 확인한다. 검증 후 `STOCK_PRICE_PUBLISH=1`을 설정한다. 공급자 네트워크 응답 및 HDFS 권한/용량도 확인한다.
4. `.service`, 두 `.timer`를 `/etc/systemd/system`에 배치한다. 환경 파일은 systemd가 읽기 때문에 운영 수동 실행도 서비스 명령을 사용하는 편이 일관적이다.

```bash
sudo systemd-analyze verify /etc/systemd/system/cosmos-stock-prices@.service \
  /etc/systemd/system/cosmos-stock-prices-kospi.timer /etc/systemd/system/cosmos-stock-prices-nasdaq.timer
sudo systemctl daemon-reload
sudo systemctl start cosmos-stock-prices@KOSPI.service
sudo systemctl start cosmos-stock-prices@NASDAQ.service
# 시장별 수동 실행 결과 검토 후 정기 실행을 등록한다.
sudo systemctl enable --now cosmos-stock-prices-kospi.timer cosmos-stock-prices-nasdaq.timer
systemctl list-timers 'cosmos-stock-prices-*'
journalctl -u cosmos-stock-prices@KOSPI.service -u cosmos-stock-prices@NASDAQ.service
```

`run-daily.sh`는 수집부터 게시/선택적 DB 적재까지 시장별 `flock`을 유지한다. 수집 출력의 마지막 JSON에서 검증된 export 경로를 읽으며 실행 문자열로 평가하지 않는다. 수집이 부분 성공하거나 업로드가 실패하면 서비스도 실패한다. 자동 즉시 재시작은 설정하지 않았다.

## 로컬 테스트

```bash
cd Crawling/prices
python -m unittest discover -s tests -p test_hdfs.py
```

실제 Parquet 검증 및 가짜 HDFS에서 성공·재실행·동시 게시 잠금·업로드/해시 실패·완료 마커 누락·기존 스냅샷 보존을 확인한다. 이 테스트는 실제 Hadoop 업로드나 systemd 등록을 검증하지 않는다.

## 운영 배포 검증 (2026-09-15)

Hadoop Master에 `/opt/cosmos/stock-prices/releases/20260915T081618Z`를 배포하고 주가 전용 가상환경을 설치했다.
배포 아카이브 SHA-256은 `4e55b85ff5d30f4928d88364cc5c2dbfaa1aaa5676864cc9a49a79fdcf89594c`다.

| 시장 | 정상 일봉 | 격리 원본 | 초기 스냅샷 |
| --- | ---: | ---: | --- |
| KOSPI | 544,903 | 16,831 | `market=KOSPI/as_of=2026-09-14/snapshot=20260915T081506Z-aa512dbf` |
| NASDAQ | 716,429 | 1 | `market=NASDAQ/as_of=2026-09-14/snapshot=20260915T080307Z-af4481c4` |

- 두 스냅샷 모두 `/data-lake/raw/stock-prices/daily` 아래에 실제 게시됐다. 각각 6개 파일의 원격 크기·SHA-256을 확인했고,
  같은 스냅샷 재게시가 `already_verified`로 끝났다. `_SUCCESS`가 있는 최종 경로만 공개했다.
- 운영 서버에서 삼성전자·AAPL의 2026-09-14 일봉을 각각 1회 직접 조회해 정상 1행을 확인했다.
- 시장별 상태 DB에 검증된 초기 자료를 적재하고 `integrity_check=ok`, 기존 `source_checked_at` 보존을 확인했다.
- 운영 서버 테스트는 124개 통과·PostgreSQL 환경 미설정 5개 제외였다. 로컬에서는 PostgreSQL 5개를 포함한 129개가 모두 통과했다.
  Bash 문법과 systemd 서비스·타이머 3개 설정 검증도 통과했다.
- 타이머는 2026-09-15 17:29 KST에 `enabled / active (waiting)`으로 등록됐다.
  최초 예정 실행은 **KOSPI 2026-09-15 18:30 KST**, **NASDAQ 2026-09-16 10:00 KST**다.
  이 기록 시점에는 최초 예약 수집이 아직 실행되지 않았다. 초기 HDFS 게시 성공과 예약 수집 실행 이력은 구분한다.
- 운영 설정은 `STOCK_PRICE_PUBLISH=1`, `STOCK_PRICE_PG_LOAD=0`이다.
  PostgreSQL 자동 적재·운영 FE/BE 배포는 이 HDFS 배포 범위에 포함되지 않는다.

예약 실행 후 결과는 `systemctl status cosmos-stock-prices@KOSPI.service`,
`systemctl status cosmos-stock-prices@NASDAQ.service`와 해당 `journalctl -u` 로그에서 확인한다.

# 차트용 국내·해외 일봉 수집

프로젝트 기업을 선택해 무료 공개 경로에서 OHLCV(시가·고가·저가·종가·거래량)를 수집한다.
API 키나 로그인은 사용하지 않는다. 기본은 **코스피 100종목 + 나스닥 100종목, 원천이 제공하는 최대 기간**이다.
수집·파일 생성, HDFS 게시, PostgreSQL 적재는 별도 명령이다. 장 마감 후 하루 한 번 실행하는 서버 설정은
`deploy/stock-prices`에서 제공한다.

```text
Naver 공식 차트 기간 API / FinanceDataReader(Yahoo)
  → 종목별 검증·SQLite 상태/원천 예외 보존
  → Parquet + 종목별 차트 JSON + 검증 manifest
    ├─ Hadoop HDFS의 시장·기준일별 원본 스냅샷
    └─ PostgreSQL stock_price_history
```

수집 결과를 사용하는 조회 API와 차트 화면은 BE·FE 담당자가 별도로 구현한다.

## 얼마나 수집할까?

장기 차트를 위해 **초기에 원천이 제공하는 최대 기간을 수집**하며 수집 범위를 10년으로 제한하지 않는다.
종목별 실제 제공 기간은 manifest에 기록한다. 기간별 조회, 52주 고저·등락률 계산과 차트 표시는
이 데이터를 사용하는 BE·FE의 연동 작업이다.

| 기간 | 200종목 예상 일봉 수 | 용도 |
| --- | ---: | --- |
| 1년 | 약 5만 행 | 기본 1년 차트, 경계 거래일 여유 별도 필요 |
| 2년 | 약 10만 행 | 이전 기본 수집 범위, 짧은 검증 실행 |
| 5년 | 약 25만 행 | 중기 추이 |
| 10년 | 약 50만 행 | 장기 추이 |
| **최대 기간** | **종목별 원천 보유 기간에 따라 결정** | **3년·5년·10년·전체 차트의 기본 수집 범위** |

연 250거래일로 계산한 예상치다. 실제 거래일·신규 상장·거래정지에 따라 줄어든다.
주봉·월봉은 일봉을 거래일의 달력 주/월로 묶어 시가=첫값, 고가=최댓값, 저가=최솟값,
종가=마지막값, 거래량=합계로 계산할 수 있다. 수집 데이터 간격은 일봉(`1D`)이며 주봉·월봉 집계는 별도 작업이다.
분봉·호가·발행 즉시 체결 데이터는 이 수집기가 제공하지 않는다.

## 설치

Python 3.11+에서 이 디렉터리를 작업 경로로 사용한다.

```bash
python -m venv .venv
# Linux: source .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py'
```

실제 PostgreSQL 테스트는 **로컬 테스트 DB**를 가리키는 `TEST_DATABASE_URL`을 설정하면 추가 실행한다.
각 테스트는 고유 스키마를 만들고 정리한다. 운영 DB로 테스트를 실행하지 않는다.

## 100종목 선택

`config/universe.json`은 `AI/ner/data/companies.csv`의 202종목과 출처 버전을 보존한다.
기본 선택은 각 시장의 기존 파일 순서 첫 100종목이다. 나스닥 기본 선택에서 제외된 것은
`WDAY`, `XEL`이며, 현재 시가총액 순위나 공식 지수 구성의 갱신을 뜻하지 않는다.

```bash
# 기본 200종목 목록 / 국내만 / 해외만
python -m services.stock_prices.cli list
python -m services.stock_prices.cli list --markets KOSPI
python -m services.stock_prices.cli list --markets NASDAQ

# 명시한 종목만 선택. 시장은 기업 목록의 값으로 결정한다.
python -m services.stock_prices.cli collect --tickers 005930 0126Z0 AAPL NVDA

# 선택 파일의 종목만 수집 (시장별 최대 100개)
python -m services.stock_prices.cli collect --selection selection.json
```

`selection.json` 예시:

```json
[{"market":"KOSPI","ticker":"005930"},{"market":"NASDAQ","ticker":"WDAY"}]
```

종목 코드는 문자열이다. 앞자리 0과 `0126Z0` 같은 영숫자 코드를 유지한다.
미등록·중복·다른 시장에 속하는 선택은 외부 요청 전에 거절한다.

## 수집과 갱신

```bash
# 기본 200종목, 원천이 제공하는 최대 기간; 옵션 생략 시에도 동일
python -m services.stock_prices.cli collect --max-history
python -m services.stock_prices.cli collect --markets KOSPI --max-history
python -m services.stock_prices.cli collect --markets NASDAQ --max-history

# 기간을 줄여 수집할 때 (1..100년 선택 가능)
python -m services.stock_prices.cli collect --years 10

# 특정 기간: 양끝 포함. 진행 중인 당일 일봉과 미래 날짜는 허용하지 않는다.
python -m services.stock_prices.cli collect --start 2024-09-01 --end 2026-09-14

# 매일 장 마감 후 갱신. 수정주가 변경을 반영하려고 기존 보존 기간 전체를 함께 재조회한다.
python -m services.stock_prices.cli collect --markets KOSPI --max-history --refresh
python -m services.stock_prices.cli collect --markets NASDAQ --max-history --refresh
```

- 권장 실행 시각: **한국 18:30 KST, 미국분 10:00 KST**, 하루 한 번씩.
  미국분은 미국 현지 전일 거래를 수집한다. 서머타임도 `America/New_York`로 처리한다.
- 날짜 기본값은 한국 18시·뉴욕 20시 이후만 현지 당일을 포함한다. 그 전에는 전일까지다.
  거래소 휴일 캘린더를 추정해 채우지 않고 원천에 있는 거래일만 보존한다.
- 정상 완료된 같은 범위는 재실행 시 캐시로 재개한다. `--refresh`도 같은 종목 성공 후 60초 이내에는 캐시를 쓴다.
- 요청 간격은 최소 1초, 종목별 별도 프로세스의 총 제한은 120초다. 기본 최대 2회 시도한다.
  401/403/429가 오면 해당 시장의 추가 요청을 중지하며 다른 경로로 우회하지 않는다.
- 빈 결과, 비정상 가격, 기존 거래일이 사라진 부분 응답은 기존 정상 스냅샷을 덮지 않는다.
  재수집 실패 기업은 이번 내보내기의 성공 대상으로 포함하지 않는다.
- 거래정지로 O/H/L=0, 종가>0, 거래량=0인 행은 원천값을 SQLite `quarantine`에 보존하고
  조회용 데이터에서 제외한다. 가짜 가격이나 거래일을 만들지 않는다.
- 국내 정수 OHLC에서 원천 종가·시가가 고저 범위를 정확히 1원 벗어난 행은
  `ohlc_range_mismatch`로 원천 그대로 격리한다. 국내 최대 이력에서 확인된 더 큰 범위 불일치는
  유한한 양의 정수 OHLC와 0 이상의 정수 거래량인 경우에만 `source_inconsistent_ohlc`로 별도 격리한다.
  O/H/L=0이고 양의 정수 종가·거래량이 있는 국내 원천 행은 `source_zero_ohl_with_volume`로 격리한다.
  이 행들을 거래정지로 분류하거나 가격을 보정하지 않는다. 격리된 원본은 HDFS에도 보존하며 차트/DB에는 넣지 않는다.
- OHLCV와 조정 종가가 모두 비어 있는 원천 행은 `source_missing_ohlcv`로 NULL을 그대로 격리한다.
  OHLCV 중 일부가 비어 있으면 `source_incomplete_ohlcv`로 남아 있는 원천값과 NULL을 함께 격리한다.
  존재하는 가격은 유한한 양수, 거래량은 0 이상의 정수여야 한다. 최신 행이 격리된 기업은 마지막 정상 거래일까지 제공하며,
  manifest의 `last_date`, `quarantined_rows`, `source_checked_at`으로 실제 제공 범위와 확인 시각을 표시한다.
- 원천의 첫 거래일이 요청 시작일보다 늦을 수 있다. 신규 상장 이전 데이터는 만들지 않는다.
- 최대 기간 수집은 1900-01-01부터 요청한다. 실제 제공 범위는 종목·출처별로 다르며 상장 이후 모든 거래일을
  보장하는 표현이 아니다. 실제 최초·최종 거래일은 manifest와 차트에 남긴다.
- 국내는 Naver 공식 차트 번들의 하한인 1980-01-01보다 이전 요청을 1980-01-01로 맞춘다.
  1998-12-07 이전 국내 토요일 거래일은 실제 원천에 있는 경우 보존한다. 그 이후 토요일과 일요일은 허용하지 않는다.
- `--max-history`, `--years`, `--start`는 동시에 지정하지 않는다.

## 결과물

상태는 `state/prices.db`, 결과는 매 실행의 고유 `output/<run_id>/` 디렉터리에 생성된다.
`--state-dir`, `--output-dir`로 경로를 지정할 수 있으며 다른 수집기의 상태를 사용하지 않는다.

- `prices_daily.parquet`: Decimal(20,4) 가격, 거래량, 거래일, 시장, 통화, 출처·가격 조정 기준.
- `charts/<market>/<ticker>.json`: `items`에 차트 연동용 숫자 OHLCV가 들어간다.
  이 파일은 종목 키로 식별하며 DB UUID를 임의로 만들지 않는다. PostgreSQL 적재 단계에서 기존 회사 UUID에 연결한다.
- `manifest.json`: 대상·성공 기업 수, 행 수, 날짜 범위, 실패 이유, 원천별 버전, 파일 SHA-256.
- `selection.json`: 이번 실행의 명시적인 종목 목록.
- `quarantine.jsonl`: 이번 정상 수집 스냅샷에서 제외한 행의 원천값과 이유. manifest에 건수와 SHA-256을 기록한다.

Parquet는 종목별로 순차 기록하여 전체 장기 데이터를 한꺼번에 메모리에 쌓지 않는다.

일부 기업이 실패하면 종료 코드는 2, manifest는 `partial`이다. 성공 기업 파일은 보존되며
실패를 완료로 표시하지 않는다. 원천·상장 이력을 확인하거나 `--selection`으로 대상을 수정한 뒤 재개한다.
`complete`는 모든 선택 기업에 제공 가능한 정상 일봉이 있다는 뜻이다. 요청한 모든 날짜의 데이터 존재를 보장하지 않으며,
기업별 실제 범위와 제외 건수는 `outcomes`에서 확인한다.

## Hadoop 저장과 하루 한 번 갱신

장기 과거 이력의 초기 적재와 장 마감 후 확정 일봉 갱신을 지원한다. 장중 분봉·체결 스트림은 수집하지 않는다.
시장별 `complete` 결과만 HDFS에 게시하며, 원본 Parquet·선택 종목·manifest·격리 원본을 함께 보관한다.

```bash
# 시장별 수집 결과 검증·게시 계획. HDFS 쓰기는 하지 않는다.
python -m services.stock_prices.cli hdfs-publish --source output/<market_run_id>

# HDFS_URI, HDFS_BIN은 서버 환경 설정으로 제공한다.
python -m services.stock_prices.cli hdfs-publish --source output/<market_run_id> --publish
```

혼합 시장 결과는 시장별로 다시 내보낸 후 게시한다. 임시 경로에서 파일 검증을 마친 뒤 고유 스냅샷 경로로 공개하며,
기존 스냅샷을 덮거나 자동 삭제하지 않는다. 요청 기준일과 실제 최초·최종 거래일을 구분해 기록한다.
스케줄·설치·실행 로그 확인은 [배포 안내](../../deploy/stock-prices/README.md)를 따른다.
한국은 18:30 KST에 당일 장 마감 데이터, 미국은 10:00 KST에 미국 현지 전일 장 마감 데이터를 수집한다.
일봉의 신규 거래일뿐 아니라 수정된 과거 주가도 재검증하기 위해 보존 기간을 함께 조회한다.

## PostgreSQL 적재와 연동용 데이터

기존 `stock_price_history` 스키마에 적재하므로 새 Flyway 마이그레이션은 필요하지 않다.

```bash
python -m services.stock_prices.cli validate --input output/<run_id>/prices_daily.parquet

# STOCK_DATABASE_URL은 호스트의 비공개 환경 설정으로 제공한다.
# 기본: 실제 SQL과 제약조건을 검사한 뒤 전체 롤백
python -m services.stock_prices.cli load --input output/<run_id>/prices_daily.parquet

# 검토한 대상 DB에 실제 반영할 때만 명시
python -m services.stock_prices.cli load --input output/<run_id>/prices_daily.parquet --commit
```

Loader는 `(market, stock_code)`로 기존 ACTIVE 회사 UUID를 정확하게 연결한다. 미등록·비활성 회사가
하나라도 있으면 가격 쓰기 전에 실패한다. 회사·스키마를 자동으로 생성하지 않는다.
일봉은 `interval_type='1D'`, 거래일은 `YYYY-MM-DDT00:00:00Z`로 저장한다.
이 시각은 날짜 표현용이며 실제 시장 종가 시각이 아니다. 한국 현지 자정을 UTC로 바꿔 전날로 밀지 않는다.
동일 회사·거래일·간격은 upsert하며 재실행 중복을 만들지 않는다. 실패하면 배치 전체가 롤백된다.
`partial` 결과는 기본 거절한다. 실패 기업을 검토한 경우에만 `--allow-partial`을 사용한다.

BE는 적재된 일봉을 회사 UUID와 거래일로 조회할 수 있다. FE·BE 담당자는 기간별 조회와 차트 화면을 연결하고,
최신성 표시에 종목별 마지막 정상 거래일을 사용해야 한다. manifest의 `source_checked_at`은 원천 확인 시각이며
거래일과 구분한다. 기간 선택과 응답 형식은 담당자가 API 명세에 반영한다.

## 가격 의미와 소스

국내 최대 이력은 Naver 공식 배포 차트 번들에 구현된 `siseJson.naver` 기간 API에서 한 시계열로 받는다.
`provider=naver_chart_sisejson`, `price_basis=naver_chart_ohlc`, `adj_close=null`로 출처를 기록한다.
최초 2년 수집에 사용한 `pykrx adjusted=True`의 fchart 경로는 삼성전자 최대 요청에서도 최신 약 3,000행만 반환했다.
기간 API는 삼성전자 1990년부터의 이력을 제공했으며, 최근 겹치는 2,999개 일봉의 모든 OHLCV가 fchart와 정확히 일치했다.
반환 OHLC를 그대로 보존한다. 응답에 조정 정책 필드가 없으므로 모든 기업·시점의 배당/권리 조정 방식을 단정하지 않는다.
과거 `end`를 지정해도 그 당시 조정 상태를 재현하는 point-in-time 자료로 보장하지 않는다.

해외는 `FinanceDataReader('YAHOO:<ticker>')` 단일 종목 경로로 원천 `Close`와 `Adj Close`를 구분한다.
`Close`를 차트의 `close_price`로 사용하고 배당을 포함한 조정 종가로 덮어쓰지 않는다.
국내 수정주가와 해외 조정 종가의 조정 항목이 같다고 가정하지 않는다. 종가 등락은 총수익률이 아니다.
FDR의 호스트 시간대·끝 날짜 처리 차이를 보정한 뒤 요청한 거래일 범위로 다시 필터한다.
Windows에서도 1970년 이전 날짜를 조회할 수 있도록 해당 Yahoo 모듈의 epoch 변환만 UTC 기준으로 적용하고 복원한다.

- [Naver 공식 차트 페이지](https://stock.naver.com/fchart/domestic/stock/005930)
- [Naver 공식 배포 차트 번들](https://financial-vn.pstatic.net/client-chart/pc/live/4.4.6/js/chartiq.js)
- [pykrx 공식 소스](https://github.com/sharebook-kr/pykrx/blob/master/pykrx/stock/stock_api.py)
- [pykrx Naver 경로](https://github.com/sharebook-kr/pykrx/blob/master/pykrx/website/naver/wrap.py)
- [FinanceDataReader Yahoo 구현](https://github.com/FinanceData/FinanceDataReader/blob/master/src/FinanceDataReader/yahoo/data.py)
- [Yahoo 조정 종가 설명](https://uk.help.yahoo.com/kb/SLN28256.html)
- [KRX KIND 공시의 토요일 휴장 실시 연혁](https://kind.krx.co.kr/external/2012/07/17/000038/20120717000087/10601.htm)

현재 고정한 라이브러리 버전과 공개 엔드포인트에서 검증했다. 비공식 경로의 미래 가용성·무제한 호출·장중 실시간성을 보장하지 않는다.

## 최대 기간 수집 결과 (2026-09-15)

기존 프로젝트 목록의 KOSPI 100종목·NASDAQ 100종목 모두 최대 기간의 정상 일봉을 확보했다.
수집 요청 완료 기준일은 `2026-09-14`이며, 최초 거래일은 종목별 원천 보유 범위에 따라 다르다.

| 시장 | 종목 | 정상 일봉 | 시장 내 가장 오래된 정상 거래일 |
| --- | ---: | ---: | --- |
| KOSPI | 100 | 544,903 | 1990-01-03 |
| NASDAQ | 100 | 716,429 | 1962-01-02 |
| 합계 | 200 | 1,261,332 | 종목별 상이 |

- 혼합 시장 Parquet: 50,674,578바이트(약 50.7MB), 종목별 차트 JSON 200개.
- 삼성전자 9,210개 일봉은 1990-01-03부터, AAPL 11,530개 일봉은 1980-12-12부터 제공한다.
- 199종목의 마지막 정상 거래일은 2026-09-14, KHC는 최신 원천 OHLCV가 없어 2026-09-11이다.
- 격리 원본 16,832행: 1원 범위 불일치 11,225, 기타 양의 정수 범위 불일치 5,034,
  OHL·거래량 0 형태 570, 거래량이 있는 OHL 0 형태 2, OHLCV 누락 1.
  이 원본을 가격 보정 없이 별도 보관하며 정상 Parquet에 포함하지 않는다.
- Python 테스트 129개가 통과했다. 실제 로컬 PostgreSQL 테스트 5개와 HDFS 게시 회귀 테스트 23개를 포함한다.
- 격리된 로컬 PostgreSQL에서 전체 dry-run 후 기존 96,773행의 해시가 유지됐다. 실제 적재로 1,164,559행을 추가해
  총 1,261,332행의 날짜·OHLCV가 Parquet와 일치했고, 동일 파일 재적재의 변경 건수는 0이었다.

PostgreSQL 적재 검증은 로컬 환경에서 수행했다. 운영 PostgreSQL 적재와 BE·FE 연동은 후속 작업이다.
운영 Hadoop에는 두 시장 모두 실제 게시했고, 각 스냅샷의 원격 해시와 동일 스냅샷 재게시를 검증했다.
하루 한 번 실행하는 타이머도 활성화했다. 최초 예정 실행과 경로는
[운영 배포 검증 기록](../../deploy/stock-prices/README.md#운영-배포-검증-2026-09-15)을 확인한다.

## 최초 2년 수집 검증 (2026-09-15)

요청 기간은 `2024-09-14`부터 `2026-09-14`까지이며 기본 선택 200종목 모두 정상 일봉을 확보했다.

| 시장 | 종목 | 정상 일봉 |
| --- | ---: | ---: |
| KOSPI | 100 | 47,908 |
| NASDAQ | 100 | 48,865 |
| 합계 | 200 | 96,773 |

- 최종 Parquet는 2,595,524바이트(약 2.6MB), 종목별 차트 JSON은 200개다.
- 거래정지 형태 17행, 국내 OHLC 범위 불일치 1행, KHC 최신일 누락 1행은 차트/DB 대상에서 제외했다.
- 199종목의 마지막 정상 거래일은 2026-09-14, KHC는 2026-09-11이다. 누락된 최신 가격을 추정하지 않았다.
- Python 테스트 85개가 통과했으며 실제 로컬 PostgreSQL 테스트 5개가 포함된다.
- 격리된 로컬 PostgreSQL에서 전체 파일의 dry-run 후 기존 983행이 유지됐고, commit 후 96,773행이 원본과 일치했다.
  동일 파일 재적재의 변경 건수는 0이었다.
- 최초 2년 수집 단계는 로컬 검증으로 마쳤다. 이후 최대 기간 수집·운영 HDFS 배포 결과는 위의 별도 기록을 따른다.

수집 데이터와 상태 DB는 저장소 밖의 작업 출력 경로에 보관한다. 실행 결과는 `manifest.json`을 기준으로 확인한다.

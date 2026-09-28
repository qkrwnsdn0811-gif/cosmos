# 코스피 100종목 네이버 뉴스 수집

`config/kospi100.json`의 기존 프로젝트 기업 100개를 검색한다. 목록은
`AI/ner/data/companies.csv`의 KOSPI 행을 추출한 고정 목록이다.
종목 코드는 `0126Z0` 같은 영숫자 6자리 문자열로 취급하고 앞자리 0을 보존한다.

## 실행 구조

- `naver_runner --mode search`: 기업명으로 최신순 검색, API 후보와 다음 검색 위치를 SQLite에 보관.
- `naver_runner --mode deliver`: 후보의 원문 본문을 추출하고 실제 기사 제목·본문에서 기업명을 확인.
  원문을 얻지 못하면 API가 제공한 네이버 뉴스 주소에서 본문을 시도한다. 각 주소의 robots 정책을 확인한다.
- 본문을 확보한 기사는 `naver_news_search` 출처로 별도 outbox → 기존 Kafka `news.raw` → 기존 HDFS Writer에 전달.
- 검색과 본문 처리는 서로 다른 프로세스다. 원문 서버의 응답 지연이 100개 기업 검색을 막지 않는다.
- 기존 국내·해외 크롤러, Writer, 큐와 서비스는 그대로 사용하며 Naver 상태는 `naver-state`로 분리한다.

### 수집 주기와 범위

기본은 기업별 최소 600초 주기다. 처음 시작할 때 100개 기업의 첫 요청을 6초씩 분산한다.
한 기업당 첫 페이지 1회만 필요한 경우 하루 약 14,400회다. API의 하루 25,000회 한도 중
이 수집기는 24,000회까지만 호출하며, 추가 페이지와 실패한 호출도 예산을 소비한다.
같은 애플리케이션을 다른 서비스가 쓰면 그 호출량도 고려해 `NAVER_DAILY_BUDGET`을 낮춰야 한다.
수동 인증 점검 요청은 별도 호출이므로 수집기 카운터에 포함되지 않는다.

API는 push가 아닌 주기적 검색이다. 네이버 색인 지연, 추가 페이지, 호출 한도, 원문 재시도로
실제 지연은 10분보다 길어질 수 있다. 첫 실행은 최근 24시간, 이후는 이전 완료 시점보다
6시간 이전부터 겹쳐 조회한다. 날짜가 없는 결과도 후보로 보존한다.
1,000건 조회 한도에 도달하면 `overflow`와 미확인 구간을 남기고 완료 시점을 전진시키지 않는다.
재시작해도 다음 기업·페이지·완료 시점·KST 일일 예산이 유지된다.

신규 기사는 발행 시각 최신순으로 처리한다. 실패 후보에도 재시도 슬롯을 배정하고,
5회 실패한 후보는 삭제하지 않고 `exhausted_candidates`로 표시한다.
기사 한 건을 확보할 때마다 Kafka 전송을 시도한다. 장애 시 outbox에 남아 다음 실행에서 재전송된다.
디스크 여유 2GiB 미만 또는 대기·재시도 가능한 후보 50,000개 이상이면 새 검색을 일시 중지한다.
재시도를 소진한 후보는 이 개수에서 제외하되 상태 DB에 남겨 확인할 수 있다.

### 기사 품질과 중복

- API `description`은 요약이며 `content`에 넣지 않는다. 본문 수집 실패를 요약으로 대체하지 않는다.
- 검색 제목·요약만 기업명을 포함하고 실제 기사 제목·본문에는 없으면 `filtered` 처리한다.
- 영문 짧은 이름의 부분 일치를 막는다. 예를 들어 `LG전자`만 있으면 별도 기업 `LG`로 매칭하지 않는다.
  한국어 조사와 전각 영문·대소문자는 처리한다. 별칭과 모호한 그룹·법인 구분은 별도 NER 단계의 범위다.
- 로그인·접근 제한 안내 및 구독 안내로 판별된 페이지를 본문으로 게시하지 않는다.
- 동일 URL을 여러 기업 검색에서 찾아도 후보·outbox는 한 건이다. 검색 기업 목록은 후보에 합친다.
  발행 시 실제 기사에서 발견한 모든 대상 기업을 `metadata.matched_companies`에 기록한다.
- 기존 다른 출처 크롤러와의 중복은 후속 처리의 URL/본문 중복 제거 대상이다.
- 출처·원문 URL·네이버 URL·검색 요약·추출 방식은 metadata에 구분해 보관한다.

## 인증과 API 모드

`NAVER_API_MODE=hub`는 `https://naverapihub.apigw.ntruss.com/search/v1/news`에
`X-NCP-APIGW-API-KEY-ID`, `X-NCP-APIGW-API-KEY` 헤더를 보낸다.
`openapi` 모드는 개발자센터 주소와 `X-Naver-Client-Id`, `X-Naver-Client-Secret` 헤더를 사용한다.
키는 명시한 한 모드에서만 사용하고 다른 서비스로 자동 재시도하지 않는다.
두 모드 모두 환경변수 이름은 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`이다.
HTTP 리다이렉트와 환경 프록시를 사용하지 않으며, API 응답·오류 로그에 인증정보를 출력하지 않는다.
원문을 가져오는 Node 프로세스에도 인증정보를 전달하지 않는다.

## 빌드와 테스트

`Crawling/news`에서 기존 의존성을 설치한 뒤 실행한다.

```bash
npm ci
npm run build
python -m unittest discover -s tests -p 'test_news_kafka_*.py' -v
npm test
python scripts/package-news.py
```

테스트는 합성 API/기사와 임시 SQLite를 사용하며 실제 키를 요구하지 않는다.
파서부터 기존 Writer의 실제 Parquet 직렬화까지 검증한다.
배포 압축은 Naver 코드·100종목 목록·본문 번들을 포함하고 키·수집 데이터·상태 DB는 제외한다.

## Master 배포

1. 빌드한 압축을 `/home/ubuntu/news-kafka/naver-app`에 풀어 서비스 계정이 읽을 수 있게 한다.
2. `deployment/naver-news.env.example`을 참고해 `/etc/cosmos/naver-news.env`를 설정한다.
   실제 키는 이 파일에만 두며 root 소유·권한 `0600`으로 관리한다.
3. `NAVER_PYTHON`은 requirements가 설치된 Python 3.11+를, `NEWS_NODE`는 Node 22.13+를 가리킨다.
4. `/home/ubuntu/news-kafka/naver-state`를 ubuntu 소유·권한 `0700`으로 생성한다.
5. `deployment/cosmos-naver-news@.service`를 `/etc/systemd/system/`에 설치하고 적용한다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cosmos-naver-news@search cosmos-naver-news@deliver
systemctl status cosmos-naver-news@search cosmos-naver-news@deliver --no-pager
```

`naver-state/search-status.json`, `deliver-status.json`에서 최근 업데이트·기업별 완료 시점·오류·예산과
전송 건수를 확인한다. `requests_today`는 이 DB를 공유하는 호출만 센다. 다른 Naver 애플리케이션의
키를 같은 DB에 적용하면 오류를 내며, 키 교체와 데이터 상태를 별도로 검토해야 한다.
중지는 두 Naver unit만 대상으로 한다. 재개할 때 `naver-state`를 유지한다.

## 2026-09-15 배포·검증

- 오프라인: Python 79개, Node 27개 테스트 통과, 두 JavaScript 번들 빌드·패키징 성공.
  실제 100종목 목록의 영숫자 코드 로딩과 배포 디렉터리 심볼릭 링크를 통한 CLI 실행도 검증했다.
- 운영 Master: 15:55:59 KST에 최종 본문 추출 수정본 적용. 최초 검색은 15:53:31 KST에 시작했고
  재배포 시 후보·회사별 진행·일일 예산을 유지했다.
- `cosmos-naver-news@search`, `cosmos-naver-news@deliver`를 enabled/active로 확인했다.
  기존 뉴스 수집기·HDFS Writer·Kafka 프로세스는 재시작하지 않았다.
- 배포 경로: `/home/ubuntu/news-kafka/naver-releases/20260915T065528Z`.
  운영 검증에 사용한 배포 압축 SHA-256은
  `54d161ac77908119dad095178cba73ca370409ff68bafc83c39251ae06b7ffbf`이다.
- 실제 API HUB 인증 200을 확인했으며 인증정보는 서버의 root 전용 `0600` 파일에만 설정했다.
  Git에는 빈 환경변수 예제만 포함한다.
- 15:57:51 KST 실제 기사 검증: `news.raw` partition 0 / offset 749의 이벤트를
  HDFS `partition=0/start=00000000000000000743` 배치의 국내 Parquet에서 확인했다.
  파일 SHA-256, outbox payload와의 일치, `source=naver_news_search`, 실제 제목·본문의 기업 매칭이 통과했다.
  확인한 기사 본문은 4,060자이며 검색 API 요약이 아니다.
- 16:03:36 KST 전체 100종목의 실제 API 응답 저장을 확인했다. 초기 조회 상태는
  `complete=95`, `overflow=5`, `paging=0`, `idle=0`이며 API 호출은 249회였다.
  `overflow`는 API 1,000건 한도로 과거 구간을 모두 확인하지 못했다는 뜻이다.
- 같은 시점 본문 147건의 Kafka ACK, 검색 오류 0건, 신규 서비스 재시작 0회를 확인했다.
  대기 9,612건·재시도 241건은 계속 처리 중이고 재시도 소진은 0건이었다.
  100종목 조회 확인과 모든 후보 본문 처리 완료는 구분한다.

## API 이용 범위 확인

API 접근 성공은 검색 데이터의 모든 저장·가공·AI 활용에 대한 허락을 뜻하지 않는다.
API HUB는 2026-09-20 시행 예정 개정 공지에 검색 데이터의 저장·캐싱 및 AI 활용 제한을 명시했다.
현재 약관에도 허용 범위를 넘는 저장·이용 제한이 있으므로, 서비스 운영자는 실제 저장·활용 범위와
별도 허락 여부를 확인해야 한다. 개발자센터 API의 2026-09-07 시행일과 혼동하지 않는다.

- [API HUB 뉴스 검색과 호출 한도](https://api.ncloud-docs.com/docs/naver-api-hub-search-news)
- [API HUB 인증 이관 안내](https://guide.ncloud-docs.com/docs/apihub-migration)
- [API HUB 사용·사용량 제한](https://guide.ncloud-docs.com/docs/apihub-application)
- [API HUB 약관 개정 공지](https://www.ncloud.com/support/notice/all/2243)

이 작업은 수집 경로를 제공하며 AI 학습·분석 작업을 시작하지 않는다.

# 마감 전 데이터 최신화 순서 (10-29 ~ 10-30에 한 번)

시연과 배포 서버를 마지막 뉴스·주가로 맞춘다. 개발 PC(로컬 `cosmos-postgres`)에서 아래 순서대로 한 번 돌리고, 끝나면 문서 숫자와
배포용 덤프를 다시 만든다. 전부 1시간 20분 안팎이다(대부분 `analyze`).

앞부분은 모두 같다: 저장소 루트에서 `nexus/.venv/Scripts/python -I -X utf8 -m nexus …`. DB 주소는
`postgresql://cosmos:cosmos1234@localhost:5432/cosmos` (아래 `$DB`).

## 0. 준비

- `docker ps` 로 `cosmos-postgres`, `cosmos-redis-6380` 이 떠 있는지 본다. 백엔드·프론트는 켜 두어도 된다.
- 되돌릴 수 있게 지금 DB를 덤프해 둔다: `docker exec cosmos-postgres pg_dump -U cosmos -d cosmos -Fc > C:\Codingdingding\nexus-db\before-refresh.dump`
- 저장소 루트 `.env` 에 `BIGKINDS_ACCESS_KEY`, `DART_API_KEY` 가 있는지 본다(값은 출력하지 않는다).

## 1. 순서

| # | 명령 | 걸리는 시간 (10-25 기준) | 하는 일 |
|---|---|---|---|
| 1 | `collect --chain semis --start 2026-10-01 --end <내일 날짜>` | 2분 | 빅카인즈 기사 수집. 겹치는 날은 캐시와 합친다 |
| 2 | `analyze --chain semis` | 46분 | 기업 인식·관계·이벤트·전파 (`nexus/data/analyzed/semis/*.pkl`) |
| 3 | `sentiment --chain semis` (**GPU 환경**: `nexus/.venv-rocm/Scripts/python -I -X utf8 -m nexus sentiment --chain semis`) | 수 분 | 새 기사만 KR-FinBERT 점수 |
| 4 | `load --chain semis --database-url $DB` | 5분 | 이벤트·영향·초과수익률(1·3거래일)·주가·뉴스 근거 문장 적재 |
| 5 | `cases --chain semis --database-url $DB` | 3분 | 뉴스 유형, 5·20거래일 초과수익률, 과거 사례, 방향 표시, 적중률, 예상 변동폭, 피드 순서. **4 다음에 반드시** (load 가 5·20거래일 행을 지운다) |
| 6 | `sentiment --chain semis --database-url $DB` (GPU 환경) | 1분 | 뉴스 화면 라벨(같은 날 상대 논조 3등분)을 다시 쓴다 |
| 7 | `fundamentals --chain semis --database-url $DB` | 3분 | 시가총액·규모(KRX), PER·PBR·배당(OpenDART) |
| 8 | `ownership --chain semis --database-url $DB` | 1분 | 공시 지분 관계(OpenDART 타법인 출자 현황, 1% 이상) |
| 9 | `publish-graph --database-url $DB` | 1분 | 관계 은하 스냅샷(뉴스 관계 + 공시 지분) |

실패하면 그 단계만 다시 돌린다. 모든 단계는 같은 결과를 다시 써도 되게 만들어져 있다.

## 2. 확인

```sql
SELECT count(*), max(dateline) FROM news_event WHERE value_chain = 'SEMICONDUCTOR';      -- 이벤트 수·마지막 뉴스
SELECT max(trading_at) FROM stock_price_history;                                          -- 마지막 거래일
SELECT horizon, labeled_count, hit_count FROM signal_backtest WHERE news_type IS NULL;     -- 적중률(문서용)
SELECT as_of_at FROM graph_snapshot WHERE status = 'PUBLISHED';                           -- 은하 기준 시각
SELECT count(*) FROM company_ownership;                                                   -- 공시 지분 관계 수
```

화면(`http://localhost:5173`): 첫 화면 피드 맨 위가 새 날짜인지, 시연 뉴스 3건(`docs/nexus/demo-scenario.md`)이 열리는지, 기업 탭 시세판의
기준일과 '지금 주목할 종목', 관계 은하 기준 시각.

## 3. 문서 숫자 고치기

- `docs/nexus/demo-scenario.md`: 9행의 이벤트 수, 7절 예상 질문의 적중률(단기·장기 표시 수와 비율, 기준선), 6절 기업 탭의 지수·주목 종목·상승/하락 수,
  시연 뉴스 카드 표(뉴스는 7~9월이라 보통 그대로지만 8절 SQL로 한 번 확인).
- `docs/nexus/case-reaction-report.md` 는 `cases` 가 다시 쓴다(커밋만).
- `docs/nexus/existing-vs-new.md`: 1절 커밋 수(`git rev-list --count pre-hackathon-baseline..nexus --`), 3절 커밋 목록.
- `docs/nexus/deploy-update.md`: 1절 표의 뉴스 데이터 기간.

## 4. 배포용 덤프 다시 만들기

```bash
docker exec cosmos-postgres pg_dump -U cosmos -d cosmos -Fc -Z 6 > C:/Codingdingding/nexus-db/cosmos-nexus-final.dump
```

빈 DB에 복원해 확인한다:

```bash
docker exec cosmos-postgres psql -U cosmos -d postgres -c "CREATE DATABASE restore_check OWNER cosmos"
docker exec -i cosmos-postgres pg_restore -U cosmos -d restore_check --no-owner --exit-on-error < C:/Codingdingding/nexus-db/cosmos-nexus-final.dump
docker exec cosmos-postgres psql -U cosmos -d restore_check -c "SELECT count(*), max(dateline) FROM news_event"
docker exec cosmos-postgres psql -U cosmos -d postgres -c "DROP DATABASE restore_check"
```

그다음 팀원에게 새 파일을 보내고, 서버에서 `deploy-update.md` 3절 ③(DB 교체)만 다시 하면 된다(코드가 그대로면 ②·④는 건너뛴다).

# nexus

2026 뉴스빅데이터 해커톤 본선용 파이프라인. 빅카인즈 기사를 받아 기업 인식, 근거 관계, 추정 전이, 초과수익률을 계산하고 PostgreSQL에 적재한다.

- 설계: `docs/superpowers/specs/2026-10-13-nexus-bigkinds-design.md`
- 빅카인즈 실측: `docs/nexus/bigkinds-api-findings.md`
- 뉴스는 빅카인즈 API로만 받는다. `Crawling/news` 등 COSMOS 크롤러는 해커톤 빌드에서 사용하지 않는다.
- 빅카인즈 응답 캐시와 Parquet은 `nexus/data/`에 저장되며 git에 올리지 않는다.

## 설치

저장소 루트에서:

```bash
python -m venv nexus/.venv
nexus/.venv/Scripts/python -m pip install -e "nexus[dev]"
```

access key는 저장소 루트 `.env`에 `BIGKINDS_ACCESS_KEY=...`로 넣는다.

## 테스트

```bash
nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests -q
```

DB 적재 테스트는 `TEST_DATABASE_URL`(예: `postgresql://cosmos:cosmos1234@localhost:5432/postgres`)이 있을 때만 실행된다. DB는 `docker compose -f BackEnd/docker-compose.yml up -d postgres`로 띄운다.

## 투자자용 계산 (2026-10-25)

`load` 다음에 실행한다. `load`가 이벤트마다 초과수익률을 1·3거래일로 다시 쓰므로 `cases`는 항상 그 뒤에 돌린다.

```bash
nexus/.venv/Scripts/python -I -X utf8 -m nexus cases --chain semis --database-url postgresql://cosmos:cosmos1234@localhost:5432/cosmos
nexus/.venv/Scripts/python -I -X utf8 -m nexus fundamentals --chain semis --database-url postgresql://cosmos:cosmos1234@localhost:5432/cosmos
```

- `cases`: 뉴스 유형 분류, 1·3·5·20거래일 초과수익률, 과거 사례 반응표(2026-06까지), 단기·장기 방향 딱지, 2026-07 이후 적중률. 보고서는 `docs/nexus/case-reaction-report.md`.
- `fundamentals`: 시가총액·규모(대형/중형/소형)는 KRX 상장 목록(FinanceDataReader)으로, PER·PBR·배당수익률은 OpenDART 재무제표로 계산한다. 저장소 루트 `.env`의 `DART_API_KEY`가 발급받은 40자리 인증키여야 하고, 아니면 재무 지표는 비워 둔다.

# 배포 서버를 최신으로 올리기 (2026-10-25)

`https://34-64-222-128.sslip.io` 를 지금 로컬 개발판과 같게 만드는 순서다. 서버에 접속할 수 있는 사람이 한 번에 따라 하면 된다.

## 1. 지금 차이

| 항목 | 배포 서버 (10-25 확인) | 최신 |
|---|---|---|
| 코드 | 10-19 무렵. 첫 화면이 관계 은하, `/api/events` 응답에 `newsType`·`signals`·`expectedMoves` 없음 | 원격 `NEXUS` `34ae365` |
| DB 스키마 | V11로 추정 (V12 투자자 화면 테이블이 응답에 없음) | V15 |
| 뉴스 데이터 | 2026-10-13까지 | 2026-10-25까지, 반도체 뉴스 115,116건 |

코드만 올리면 V12~V15 테이블은 생기지만 비어 있다. 예상 변동폭, 과거 경향, 뉴스 유형, 피드 순서, 감성 라벨, 근거 문장은
로컬 `nexus` 파이프라인이 채운 값이라 **DB도 함께 옮겨야** 화면이 로컬과 같아진다.

## 2. 준비물

- 코드: 원격 `NEXUS` 브랜치 (`34ae365` 이후)
- DB 덤프: `cosmos-nexus-2026-10-25.dump` (123MB, PostgreSQL 17 custom 형식, 로컬 DB 전체)
  - 개발 PC의 `C:\Codingdingding\nexus-db\` 에 있다. 저장소에는 넣지 않는다. 서버로 직접 옮긴다(scp 등).
  - 계정·커뮤니티 글·스크랩은 0건이라 개인정보는 없다. API 키는 DB가 아니라 `.env`에 있어 덤프에 없다.
  - 로컬에서 빈 DB에 복원해 확인했다: 뉴스 115,116건, 마지막 뉴스 10-25, Flyway V15.

## 3. 순서

서버의 COSMOS 배포가 `deploy/compose.yml`(Compose 프로젝트 이름 `cosmos`)로 떠 있다고 가정한다. 다르게 띄웠다면
`docker ps` 로 postgres 컨테이너 이름을 찾아 아래 `$PG` 자리에 넣는다.

```bash
PG=$(docker ps -q --filter label=com.docker.compose.project=cosmos --filter label=com.docker.compose.service=postgres)
```

**① 서버 DB 백업** — 되돌릴 때 쓴다. 서버에서 가입한 계정·글이 있다면 여기에만 남는다.

```bash
docker exec "$PG" pg_dump -U cosmos -d cosmos -Fc > ~/cosmos-before-nexus-update.dump
```

**② 코드 받고 이미지 빌드** — 배포에 쓰는 저장소에서.

```bash
git fetch origin && git checkout NEXUS && git pull --ff-only origin NEXUS
bash deploy/scripts/build-images.sh nexus-34ae365
```

**③ DB 교체** — 이 몇 분 동안 화면이 오류를 낸다. 덤프에 Flyway 기록(V15까지)이 들어 있어 새 백엔드는 마이그레이션을 다시 돌리지 않는다.

```bash
docker exec "$PG" psql -U cosmos -d postgres -c "DROP DATABASE cosmos WITH (FORCE)" -c "CREATE DATABASE cosmos OWNER cosmos"
docker exec -i "$PG" pg_restore -U cosmos -d cosmos --no-owner --exit-on-error < ~/cosmos-nexus-2026-10-25.dump
```

**④ 새 이미지로 배포**

```bash
bash deploy/scripts/deploy.sh nexus-34ae365
```

`deploy.sh` 는 `/etc/cosmos/app.env` 가 없으면 실행 중인 백엔드 컨테이너에서 환경값을 되살린다. 그래서 ③에서 백엔드를 멈추지 않는다.
Compose를 직접 띄웠다면 이미지 태그만 `nexus-34ae365` 로 바꿔 `docker compose up -d` 한다.

## 4. 확인

```bash
curl -s "https://34-64-222-128.sslip.io/api/events?valueChain=SEMICONDUCTOR&size=1"
```

- 응답 항목에 `newsType`, `signalCounts` 가 있고, 첫 뉴스 날짜가 10-25다.
- 첫 화면(`/`)이 이벤트 피드이고, 상단 메뉴가 '관계 은하 · 이벤트 · 뉴스 · 기업 · 커뮤니티' 순서다.
- 시연 뉴스가 열린다: `/?event=b8b8e847-3d08-5f69-a04a-9fde01ca1edd` (케이씨텍 공급계약, [시연 시나리오](demo-scenario.md) ①).

## 5. 되돌리기

③의 명령에서 덤프 파일만 `~/cosmos-before-nexus-update.dump` 로 바꿔 복원하고, 이전 이미지 태그로 `deploy.sh` 를 다시 돌린다.
이전 태그는 `/opt/cosmos/releases/current` 에 남아 있다(`deploy.sh` 로 배포했을 때).

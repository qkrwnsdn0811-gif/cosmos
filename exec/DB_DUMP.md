# DB 덤프

| 항목 | 값 |
| --- | --- |
| 파일 | `exec/db/cosmos_web.dump.part-aa` ~ `part-ae` (5개) (합치면 `cosmos_web.dump`, 228,646,308 bytes) |
| SHA-256 | `44a49af39a6649498dd6102f82a2be4edb7b41739840fcd814ec25677f540769` |
| 생성 시각 | 2026-09-28 02:01 UTC, 운영 DB `cosmos` (PostgreSQL 17.11) |
| 형식 | `pg_dump -Fc -Z zstd:9 --no-owner --no-privileges` (PostgreSQL 17 이상의 `pg_restore` 필요) |
| 복원 검증 | 빈 DB에 `pg_restore --exit-on-error`로 복원 성공, 55초 |

GitLab 파일 크기 제한 때문에 45 MiB씩 나누어 올렸습니다.

## 들어 있는 것

- `public` 스키마: 웹 서비스가 읽는 30개 테이블 전체 데이터 (3,050,261행). 기업 202개, 뉴스 452,335건, 공시, 관계·점수, 주가 일봉, 그래프 스냅샷, 커뮤니티 댓글, Flyway 이력(V1~V9).
- `cosmos_analysis` 스키마: 테이블·뷰 **구조만** (17개 테이블, 데이터 없음). 분석 파이프라인의 중간 적재용이고 백엔드 API는 읽지 않습니다. 운영에서는 3.9 GB라 제외했습니다.
- 뺀 것: 운영 중 임시로 만든 백업 테이블 `relationship_score_current_bak_20260922`, `source_document_published_fill_20260922`. 테이블 소유자·GRANT (DB 계정을 새로 만들어 쓰도록).

## 복원

```bash
# 1) 파일 합치고 확인
cat exec/db/cosmos_web.dump.part-* > cosmos_web.dump
sha256sum cosmos_web.dump            # 위 SHA-256과 같아야 합니다

# 2) 빈 DB 준비 (로컬: BackEnd/docker-compose.yml의 cosmos-postgres 컨테이너)
docker compose -f BackEnd/docker-compose.yml up -d postgres
docker exec cosmos-postgres dropdb -U cosmos --if-exists cosmos
docker exec cosmos-postgres createdb -U cosmos cosmos

# 3) 복원
docker exec -i cosmos-postgres pg_restore -U cosmos -d cosmos --no-owner --no-privileges --exit-on-error < cosmos_web.dump
```

- 복원 후 백엔드를 띄우면 Flyway가 V9까지 적용된 것을 확인하고 그대로 시작합니다 (`validate-on-migrate`).
- 운영 서버에 복원할 때는 기존 볼륨 `cosmos_postgres_data`를 먼저 백업합니다. 이 덤프는 `cosmos_analysis`가 비어 있으므로 운영 DB를 덮어쓰는 용도가 아니다.
- Loader 계정(`cosmos_loader`, `cosmos_document_loader`, `cosmos_stock_loader`)은 포함하지 않았습니다. 데이터 파이프라인까지 연결할 때 `docs/rdb-loader.md`, `deploy/document-loader/README.md`의 GRANT를 적용합니다.

## 다시 만드는 방법

운영 DB에서 직접 덤프하면 개인정보가 들어가므로 임시 DB를 거칩니다. 웹 EC2(`j15c205.p.ssafy.io`)에서 실행합니다.

```bash
D="sudo docker exec -i cosmos-postgres-1"
EX="--exclude-table=public.relationship_score_current_bak_20260922 --exclude-table=public.source_document_published_fill_20260922"
$D createdb -U cosmos cosmos_export
$D sh -c "pg_dump -U cosmos -d cosmos -s --no-owner --no-privileges $EX | psql -U cosmos -d cosmos_export -q -v ON_ERROR_STOP=1"
$D sh -c "pg_dump -U cosmos -d cosmos -n public -a --disable-triggers $EX | psql -U cosmos -d cosmos_export -q -v ON_ERROR_STOP=1"
$D psql -U cosmos -d cosmos_export -v ON_ERROR_STOP=1 \
  -c "CREATE EXTENSION pgcrypto" \
  -c "UPDATE users SET email = 'demo' || user_id || '@example.com', password = crypt('CosmosDemo2026', gen_salt('bf', 10))" \
  -c "DROP EXTENSION pgcrypto"
$D pg_dump -U cosmos -d cosmos_export -Fc -Z zstd:9 --no-owner --no-privileges > cosmos_web.dump
$D dropdb -U cosmos cosmos_export
split -b 45m cosmos_web.dump cosmos_web.dump.part-   # 필요하면 분할
```

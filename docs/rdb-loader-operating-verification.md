# RDB Loader 운영 연결·Flyway 적용 검증

검증일: 2026-09-15, 최종 확인 05:44 UTC / 14:44 KST.

## 검증 범위와 결과

운영 연결과 마이그레이션 적용, 실제 HDFS를 사용하는 격리 적재 테스트를 완료했다.
운영 집계·특징 경로가 비어 있어 사용자 동의에 따라 합성 데이터와 별도 스키마를 사용했다.
운영 `public` 테이블에 합성 그래프를 게시하지 않았다.

| 항목 | 확인 결과 |
| --- | --- |
| Main PostgreSQL | PostgreSQL 17.11, 기존 `cosmos` DB·영속 볼륨 유지 |
| 운영 Flyway | 배포 앱의 Flyway 12.4.0으로 V4 → V5 적용, 이후 validate 성공 |
| Flyway 재실행 | 추가 마이그레이션 0개, V1~V4 이력과 체크섬 유지 |
| V5 | `graph_snapshot_load` 생성, checksum `2040526964`, success=true |
| DB 게시 주소 | Main Tailscale `100.69.73.112:5432`에만 게시 |
| 접근 제어 | Master `100.117.115.44` 접속 성공, Worker 3대 접속 차단 |
| 영구 설정 재적용 | Docker guard 재실행 후 동일한 허용·차단 확인 |
| 운영 Loader 계정 | `cosmos_loader`, superuser/createdb/createrole/replication 권한 없음 |
| 집계 생성 | Master Spark 4.2.0 `local[2]` → 실제 HDFS Parquet/manifest/_SUCCESS |
| Loader 실행 | Master systemd → 실제 HDFS 다운로드 → Main 격리 스키마 |
| 적재 | 원본 6행, 양쪽 NULL인 1행 제외, CURRENT/HISTORY 각각 5행 |
| 재실행 | `ALREADY_PUBLISHED`, 현재 점수·이력·스냅샷·영수증 건수 유지 |
| 실패 롤백 | 테스트 트리거가 최종 PUBLISHED 갱신을 거부해도 이전 5행과 이력 보존 |
| 그래프 API | 변경된 backend jar를 별도 loopback 테스트 컨테이너로 실행, 200·노드 3개·관계선 2개 |
| 관계 상세 API | 30D 구성 점수·기본 점수·NULL 응답 일치 |
| 정리 | 테스트 API 컨테이너·스키마·HDFS 입력/출력·서비스별 테스트 설정 삭제 확인 |
| 운영 상태 | 기존 backend `/actuator/health` UP, 서비스 데이터 건수 0 유지 |

## 점수 검증

검증 스냅샷 ID는 `05b5c109-ec72-471e-b9b1-75796393ceb8`이었다.
입력은 중복 1건을 포함한 4개 문서 특징 행이며, 고유 문서 근거 3건이다.

| 관계 | 기간 | news_score | disclosure_score | score | 결과 |
| --- | --- | --- | --- | --- | --- |
| TEST A → B, SUPPLY | 7D·30D·90D | 80 | 20 | 50 | 각 기간 적재 |
| TEST A → C, PARTNER | 7D | NULL | NULL | NULL | 적재 제외 |
| TEST A → C, PARTNER | 30D·90D | 0 | NULL | 0 | 실제 0점으로 적재 |

파일 SHA-256: `913b30e229b48ee94e3672e9cd6e92ba35f722c4ea3a4a8b174bf5bfd7281499`.
검증 manifest 해시: `43bbae5407c8a2d05df8a367afb347f8215b30f20f305335b7a1833a765eb368`.

## 운영 설치와 증빙 위치

- Master 코드: `/home/ubuntu/cosmos-rdb-loader/releases/20260915-053417`.
- Master Python: 위 경로의 `.venv-rdb-loader/bin/python`.
- Loader unit: `/etc/systemd/system/cosmos-rdb-loader@.service`.
- 실행 설정: `/etc/cosmos/rdb-loader.env`, DB 인증은 서비스 계정만 읽는 `rdb-loader.pgpass`.
- 기본 운영 입력: `hdfs://100.117.115.44:9000/data-lake/aggregated/relationship-scores/<snapshot UUID>`.
- Main Compose override: `/etc/cosmos/rdb-loader.compose.yml`.
- Main 네트워크 백업·증빙: `/var/backups/cosmos-rdb-loader-access/20260915T053508Z`.
- Main Flyway 백업·증빙: `/home/ubuntu/cosmos-rdb-loader-deploy-20260915/public`.
- 마이그레이션 전 DB 백업: `cosmos-public-20260915T053748Z.dump`, 74,803 bytes, 권한 0600.
- 백업 SHA-256: `08245895ecd9b886dd290114d3c29e2241d3f3ed6e75d240b47344111c0492fa`.

별도 스키마 `rdb_loader_verify_20260915_053417`도 실제 Flyway로 V1~V5를 구성해 검증했다.
정리 후 이 스키마와 합성 데이터는 남아 있지 않다. 운영 전용 계정·연결·V5·Loader unit은 유지했다.

## 후속 작업

- 실제 기업·관계 유형 기준정보와 뉴스·공시 특징/집계 산출물을 준비해 운영 그래프를 게시한다.
  현재 실제 데이터가 없으므로 공개 `/api/graphs/latest`의 `GRAPH_SNAPSHOT_NOT_FOUND`는 유지된다.
- NodeManager 3대가 `SHUTDOWN` 상태라 YARN 실행은 검증하지 않았다. 운영 배치 실행 정책에 따라
  NodeManager를 가동하고 `run-pipeline.sh`의 기본 YARN 경로를 검증한다.
- 현재 웹 앱 코드는 이번 작업으로 교체하지 않았다. 새 backend jar는 격리 컨테이너에서 검증했다.
  MR 병합 후 정상 배포로 그래프 조회의 REPEATABLE READ 변경을 적용한다.
- 이후 웹 배포에서도 DB 접속이 유지되도록 MR의 `deploy/scripts/deploy.sh`를 사용해야 한다.
  이 스크립트는 운영에 설치한 Compose override를 매 릴리스에 포함한다.

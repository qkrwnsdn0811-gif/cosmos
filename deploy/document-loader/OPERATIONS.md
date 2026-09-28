# 2026-09-16 운영 반영 기록

## 적용한 구성

- 운영 PostgreSQL `cosmos.public`에 신규 V6를 실제 Flyway로 적용했다. V1–V5 체크섬은 그대로이며, 적용 후 validate 및 두 번째 migrate(추가 적용 0건)를 확인했다.
- 적용 전 DB 전체 백업을 DB 서버의 `/var/backups/cosmos-document-loader/20260916-pre-v6/cosmos-before-v6.dump`에 보관했다. 백업 목록 검사도 통과했다.
- 비어 있던 기업 기준정보에 기존 `AI/ner/data/companies.csv`의 202개 종목(KOSPI 100, NASDAQ 102)을 등록했다. 주가 수집 대상의 100+100 선택 설정은 이 기준정보 등록과 별개다.
- 실행 계정 `cosmos_document_loader`를 생성하고 문서 적재에 필요한 테이블·컬럼 권한만 부여했다. 접속 비밀번호는 저장소 밖의 보호된 파일에 보관한다.
- Hadoop Master에 `/opt/cosmos/document-loader/releases/20260916-r3`를 설치했다. 전용 가상환경을 사용하며 `current` 링크가 이 릴리스를 가리킨다.
- `cosmos-document-loader.service`와 부팅 시 활성화되는 `cosmos-document-loader.timer`를 설치했다. 한 회차 최대 50개 입력을 처리한 뒤 2분 후 다시 실행한다.

## 실제 데이터 검증

국내 뉴스 14건·해외 뉴스 1건·DART 3건·SEC 1건을 **실제 HDFS에서 읽어 운영 DB에 적재**했다. 각 입력은 먼저 실제 SQL을 롤백해 검증했고, 커밋 후 재실행에서 중복이 생기지 않음을 확인했다.

서로 다른 수집기에서 가져온 같은 기사 3개가 다른 본문 해시를 갖는 것을 확인했다. 이 3개의 최신 입력 배치를 먼저 적재한 뒤 과거 배치를 dry-run하여 최신 본문을 유지하는지 확인했다. 잘못된 입력을 성공으로 표시하거나 영수증을 임의 생성하지 않았다.

새 입력 자동 탐색과 서비스 실행을 검증했다. 첫 자동 실행에서 사전 검증 대상에 없던 뉴스 배치를 발견하여 12건을 추가 적재한 기록을 확인했다. 초기 대기분은 서비스가 계속 처리한다. 실행 중인 회차의 결과는 journal에서, 끝난 회차의 집계는 `last-run.json`에서 확인한다.

이어서 자동 서비스가 SEC 과거 배치 **16,133건**, DART 과거 스냅샷 **59,760건**을 각각 한 트랜잭션으로 적재했다. SEC의 공동 접수번호 4개는 CIK가 다른 별도 문서로 저장됐으며 공시 기업 연결 누락은 0건이었다.

**2026-09-16 10:51 KST 조회 기준** 저장 문서는 총 **75,968건**이다. 이후에도 초기 자료를 계속 처리하므로 아래 수치는 당시의 확인값이다.

| 유형 | 저장 건수 |
|---|---:|
| 국내·해외 뉴스 | 70 |
| DART 공시 | 59,763 |
| SEC 공시 | 16,135 |

해당 시점에 완료 체크포인트는 11개였으며, 첫 자동 회차의 나머지 입력과 이후 회차의 대기 입력은 계속 처리 중이다. 과거 자료 전량 적재가 끝났다는 의미는 아니다.

기존 뉴스 API의 HTTP 200 응답과 적재한 뉴스 조회를 확인했다. 백엔드·프론트엔드·PostgreSQL 컨테이너는 healthy 상태를 유지했다. V1–V5만 포함한 기존 애플리케이션의 Flyway 기본 검증도 V6 DB에 대해 읽기 전용으로 통과했다.

## 초기 적재 규모와 남은 처리

사전 조사 시 뉴스 완료 배치는 약 1,700개, DART 완료 스냅샷은 13개(문서 합 81,315건, 중복 제거 전), SEC 과거 배치는 16,133건, SEC 증분 공시는 29건이었다. 수집기는 계속 새 입력을 게시하므로 목록 수는 증가할 수 있다.

**자동 적재 연결 완료와 과거 자료 전체 적재 완료는 다르다.** 초기 자료는 배치 단위로 커밋하며 서비스가 계속 처리한다. 오류 입력은 기록한 뒤 다른 입력을 처리하고 다음 회차에 재시도한다. 새 본문으로 이미 저장한 뉴스를 교체해야 하는 경우에는 기존 수정·재분석 보호 규칙이 적용된다.

현재 수치와 실패 여부는 다음 명령으로 확인한다.

```bash
sudo systemctl status cosmos-document-loader.service cosmos-document-loader.timer
sudo journalctl -u cosmos-document-loader.service --no-pager -n 50
cat /var/lib/cosmos-document-loader/last-run.json
```

```sql
SELECT document_type, count(*) FROM source_document GROUP BY document_type;
SELECT filing_system, count(*) FROM disclosure GROUP BY filing_system;
SELECT count(*) AS committed_batches FROM document_load_batch;
```

한 회차의 `status=complete`만으로 전체 적재 완료를 판단하지 않는다. `deferred`, `failed`, `discovery_errors`도 함께 확인한다. 운영 트랜잭션은 현재 처리 중인 입력이 끝날 때까지 외부 조회에 보이지 않는다.

## 성능 및 회귀 검증

- DB 통합 테스트를 포함해 121개 테스트를 실행했다. 120개 통과, Windows 심볼릭 링크 생성 권한 제한으로 1개를 생략했다.
- DART의 수백 개 파일을 개별 HDFS 명령으로 조회하는 비용을 줄이기 위해 raw/data 목록을 각각 한 번 읽어 크기를 캐시했다. 기존 크기 비교와 입력·정규화 결과·영수증 해시는 유지한다.
- 이 보완의 추가 회귀를 포함한 소스 테스트 38개는 배포 서버 Linux에서 모두 통과했다. 운영 자동 실행의 설정·탐색 사전 검증에서도 오류가 없었다.

테스트용 로컬 PostgreSQL은 검증 후 종료했다. 실제 운영 서비스는 유지한다. 재설정·재시도·정지 방법은 [운영 실행 문서](README.md)를 참고한다.

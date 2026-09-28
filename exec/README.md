# exec 제출 자료

프로젝트를 넘겨받은 사람이 GitLab에서 클론한 뒤 빌드·배포하고 서비스를 시연할 수 있도록 모은 자료입니다. 버전과 설정은 `dev` `3953408`의 코드와 운영 서버 실측값(2026-09-28)을 기준으로 적었습니다.

| 번호 | 항목 | 파일 |
| --- | --- | --- |
| 1 | 포팅 매뉴얼: 빌드·배포 문서 (JVM·웹서버·WAS·IDE 버전, 환경 변수, 배포 특이사항, DB 계정·속성 파일 목록) | [`PORTING_MANUAL.md`](PORTING_MANUAL.md) |
| 2 | 외부 서비스 정보 (가입·키 발급·설정 위치) | [`EXTERNAL_SERVICES.md`](EXTERNAL_SERVICES.md) |
| 3 | DB 덤프 최신본 (비식별화, 분할 파일) | [`DB_DUMP.md`](DB_DUMP.md), `db/cosmos_web.dump.part-*` |
| 4 | 시연 시나리오 | [`DEMO_SCENARIO.md`](DEMO_SCENARIO.md) |

- 비밀번호, API 키, 토큰, 실제 `.env` 파일은 넣지 않았습니다. 변수 이름, 보관 위치, 발급 방법만 적었습니다.
- DB 덤프는 개인정보(이메일·비밀번호 해시)를 비식별화했고, 빈 DB에 복원되는 것을 확인했습니다.

관련 상세 문서: [`docs/CICD.md`](../docs/CICD.md), [`docs/HADOOP_CLUSTER.md`](../docs/HADOOP_CLUSTER.md), [`docs/infrastructure-design.md`](../docs/infrastructure-design.md), [`docs/database-design.md`](../docs/database-design.md), [`deploy/.env.example`](../deploy/.env.example).

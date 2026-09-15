# COSMOS EC2 CI/CD 운영 가이드

관련 이슈: [S15P21C205-116](https://ssafy.atlassian.net/browse/S15P21C205-116)

웹 서비스는 SSAFY 기본 EC2에서 Docker Compose로 실행한다. Jenkins 컨트롤러는 작업을 관리하고 같은 호스트의 별도 `cosmos-ci` 빌드 에이전트가 테스트·이미지 빌드·배포를 수행한다. 데이터 클러스터의 배치는 `infrastructure-design.md`를 따른다.

## 구성과 포트

| 구성 | 주소/포트 | 공개 범위 |
| --- | --- | --- |
| 웹 | `https://j15c205.p.ssafy.io` | HTTPS 공개 |
| Jenkins | `https://j15c205.p.ssafy.io/jenkins/` | HTTPS, 로그인 필수 |
| Spring Boot | `127.0.0.1:18081` | 호스트 Nginx만 접근 |
| 프론트 Nginx | `127.0.0.1:18080` | 호스트 Nginx만 접근 |
| Jenkins 컨트롤러 | `127.0.0.1:18090` | 호스트 Nginx·WebSocket 에이전트 |
| PostgreSQL | Docker 내부 `postgres:5432` | 외부 포트 연결 없음 |
| Redis | Docker 내부 `redis:16379` | 외부 포트 연결 없음 |

UFW는 활성 상태로 유지하고 22/80/443만 허용한다. Docker 포트 공개는 UFW를 우회할 수 있으므로 내부 서비스는 `127.0.0.1`에만 연결하거나 포트를 공개하지 않는다. Jenkins의 TCP agent 포트 50000은 사용하지 않는다.

현재 인증 구현에는 Redis가 필요하다. 프론트 이미지는 `VITE_API_MODE=live`, `VITE_API_BASE=/api`를 빌드 시 설정한다. 런타임 환경변수 변경으로 이미 만들어진 Vite 번들의 모드를 변경할 수는 없다.

## Jenkins에 GitLab 연결

관리자 계정의 ID·비밀번호는 서버 `/etc/cosmos/jenkins/admin-user`, `admin-password`에 있다. 저장소에 기록하거나 빌드 로그에 출력하지 않는다.

1. GitLab 프로젝트에서 읽기 전용 Deploy Token을 만든다. 범위는 `read_repository`이며 프로젝트 종료와 인계 일정을 고려해 만료일을 정한다.
2. Jenkins 로그인 → Manage Jenkins → Credentials → System → Global credentials → Add Credentials.
3. Kind는 `Username with password`, Username은 GitLab이 표시한 **Deploy Token 사용자명**, Password는 토큰 값, ID는 **`cosmos-git-read`**로 입력한다. 개인 로그인 사용자명을 대신 넣지 않는다.
4. `cosmos-ci-116`을 Build Now로 실행해 작업 브랜치 전체 검증을 확인한다. 이 작업에는 운영 배포 단계가 실행되지 않는다.
5. 팀 리뷰 후 MR을 `dev`에 병합한다. `cosmos-web`은 `dev`의 Jenkinsfile을 읽으므로 병합 전에는 실행하지 않는다.
6. GitLab 프로젝트 Webhook에 아래 URL과 서버 `/etc/cosmos/jenkins/webhook-token` 값을 등록한다. Push events와 SSL verification을 사용한다.

```text
https://j15c205.p.ssafy.io/jenkins/project/cosmos-web
```

Webhook은 `dev` push만 처리한다. Merge Request 이벤트와 브랜치 삭제 이벤트는 배포를 시작하지 않는다. API 권한이 없는 Deploy Token은 Webhook 생성용 토큰으로 사용할 수 없다. Webhook 등록은 권한 있는 프로젝트 관리자가 수행한다.

Webhook을 사용할 수 없다면 `cosmos-web` Configure → Poll SCM을 `H/5 * * * *`로 설정할 수 있다. 이는 대체 방식이며 기본 설정은 자동 polling을 켜지 않는다. 컨트롤러 재시작 때 UI에 등록한 credential과 기존 Job 설정은 보존한다.

## 파이프라인

1. 대상 커밋을 깨끗한 작업 공간에 체크아웃한다.
2. 프론트 TypeScript/Vite build와 ESLint를 수행한다.
3. Java 21 빌드 환경에서 백엔드 jar를 생성한다.
4. 운영망과 분리된 임시 PostgreSQL·Redis를 띄워 백엔드 테스트를 수행한다. 테스트 결과 XML은 Jenkins에 남기고 임시 테스트 환경은 정리한다.
5. 커밋 해시와 빌드 번호로 프론트·백엔드 이미지에 태그를 붙인다.
6. `cosmos-web`의 `dev` 빌드에 한해서 최신 원격 커밋인지 다시 확인하고 배포한다.
7. 백엔드·프론트 컨테이너와 외부 HTTPS 응답을 확인하고 성공 버전을 기록한다.

애플리케이션 배포는 `deploy/scripts/deploy.sh`로 실행한다. 파일 잠금으로 동시에 두 배포가 수행되지 않게 한다. DB·Redis 볼륨은 애플리케이션 교체와 별도로 유지한다. 배포 중에는 짧은 서비스 중단이 발생할 수 있으며 무중단 배포를 보장하지 않는다.

## 상태와 복구

- 릴리스 구성: `/opt/cosmos/releases/<IMAGE_TAG>/compose.yml`
- 마지막 정상 릴리스: `/opt/cosmos/releases/current`
- 운영 환경변수: `/etc/cosmos/app.env` (`root:cosmos-ci`, 0640)
- PostgreSQL 볼륨: `cosmos_postgres_data`
- Redis 볼륨: `cosmos_redis_data`
- Jenkins 볼륨: `cosmos_jenkins_home`

새 컨테이너가 시작되지 않거나 상태 확인에 실패하면 직전 정상 릴리스의 이미지·Compose 구성으로 복구한다. SIGTERM/SIGINT에도 복구를 시도한다. 강제 전원 종료나 SIGKILL 이후에는 상태 확인과 수동 복구가 필요하다. 첫 배포에는 이전 버전이 없으므로 실패 시 자동 복구 대상도 없다.

```bash
# 서버에서 이미지가 존재하는 이전 릴리스로 복구
sudo -u cosmos-ci bash deploy/scripts/deploy.sh <previous-image-tag>
```

Flyway는 앱 시작 시 DB를 변경한다. 이미지 롤백은 DB 마이그레이션을 되돌리지 않는다. 자동 배포에서는 이전 앱과 호환되는 추가형 변경만 사용한다. 컬럼 삭제·이름 변경 등 파괴적인 변경은 별도 검토·백업·복구 계획이 필요하다. 정상·직전 릴리스 이미지를 확인하기 전에는 이미지 정리 명령을 실행하지 않는다.

운영 DB 백업은 `pg_dump`로 별도 경로에 보관하고 서버 밖에도 복제한다. Jenkins 홈과 `/etc/cosmos`도 접근 제한된 백업이 필요하다. 이 저장소의 초기 설치 스크립트는 외부 백업 계정이나 정책을 임의로 만들지 않는다.

## 서버 최초 구성

스크립트 실행 전 현재 서비스와 포트, 데이터 존재 여부를 확인한다. 기존 서비스가 있는 서버에 초기 설치 설정을 그대로 적용하지 않는다.

```bash
sudo bash deploy/scripts/bootstrap-host.sh
# 최초 인증서 발급: 도메인 접근과 ACME 서비스 이용 조건을 확인한 운영자가 실행한다.
sudo certbot certonly --webroot -w /var/www/cosmos-acme -d j15c205.p.ssafy.io
sudo bash deploy/scripts/start-jenkins.sh
sudo bash deploy/scripts/enable-https.sh
```

Certbot timer가 인증서를 갱신하고 deploy hook이 Nginx를 다시 읽는다. `nginx -t`, `systemctl status certbot.timer`로 상태를 확인한다.

빌드 에이전트 `cosmos-ci`는 Docker 그룹 권한으로 호스트의 컨테이너를 관리한다. 이는 호스트 관리자에 준하는 권한이므로 신뢰하는 팀 저장소의 코드만 실행한다. 컨트롤러에 Docker 소켓을 연결하지 않는 것만으로 악의적인 빌드 코드까지 격리되는 것은 아니다. 외부 PR을 실행하는 환경으로 확장한다면 빌드 호스트를 별도로 분리한다.

## 향후 AWS 데이터 처리 연결

웹 배포와 데이터 작업 배포·실행은 서로 다른 Jenkins Job으로 구성한다. 웹 배포가 Hadoop, Kafka 또는 NameNode를 재시작하지 않도록 한다.

- 데이터 코드 배포 Job: 버전이 지정된 Collector·Writer·Loader·Spark 산출물을 SSAFY Master에 전달한다.
- 데이터 실행 Job: Master에서 `spark-submit --master yarn --deploy-mode client`를 실행한다. 데이터 경로·실행 범위·스케줄은 각 작업의 입력 계약이 확정된 뒤 설정한다.
- Loader: 성공한 HDFS 결과를 staging에 적재·검증한 후 `PUBLISHED` snapshot을 공개한다.
- Master와 AWS Worker 3대는 공통 Tailscale 주소/DNS와 필요한 접근 규칙을 먼저 검증한다.
- PostgreSQL에 Master를 연결할 때는 Tailscale IP에 한정한 포트 연결과 별도 Loader 계정을 추가한다. 현재 기본 Compose는 원격 DB 접근을 열지 않는다.
- Worker는 HDFS 저장소도 담당하므로 수집 중에는 가동을 유지한다. 실제 바이트 용량·메모리·CPU 크레딧을 측정한 후 AWS 사양을 결정한다.

AWS 리소스 생성, Tailscale 가입, 데이터 작업 스케줄은 이 웹 CI/CD 설치에서 자동 수행하지 않는다. AI 학습·분석이나 대량 재처리를 코드 push마다 실행하지 않는다.

## 검증 명령

```bash
bash deploy/tests/deploy-test.sh
bash deploy/scripts/ci.sh <test-tag>
bash deploy/scripts/build-images.sh <release-tag>
COSMOS_SMOKE_URL=https://j15c205.p.ssafy.io bash deploy/scripts/deploy.sh <release-tag>
```

`deploy-test.sh`는 가짜 Docker/curl로 정상 배포·실패·복구 실패·신호 종료·잘못된 태그를 검증하며 실제 컨테이너를 조작하지 않는다.

## 최초 설치 검증 기록 — 2026-09-14

- EC2의 Java 21 Docker 환경에서 백엔드 17개 suite, 85개 테스트 통과(실패·오류·skip 0).
- 프론트 ESLint 및 실제 API 모드의 TypeScript/Vite 빌드 통과.
- 배포·롤백 스크립트의 8개 시나리오와 ShellCheck 통과.
- Jenkins의 Declarative Pipeline 검증 API에서 Jenkinsfile 문법 검증 통과.
- 컨트롤러 실행기 0, WebSocket 에이전트 실행기 1 및 online 확인. 익명 API 접근은 403, CSRF 활성.
- 최초 앱 릴리스 `3e6b3f4-bootstrap`을 수동 배포하고 HTTPS `/healthz`의 `UP` 응답 확인.

Jenkins의 GitLab 체크아웃 및 Webhook을 통한 전체 자동 배포는 사용자가 Deploy Token·Webhook을 등록하고 MR을 `dev`에 병합한 뒤 검증해야 한다. 위 테스트는 이 자동 배포 검증을 대신하지 않는다.

새 운영 DB에는 공개된 그래프 스냅샷이 아직 없다. `/api/graphs/latest`의 `GRAPH_SNAPSHOT_NOT_FOUND` 응답과 프론트의 그래프 로딩 실패 표시는 현재 데이터 미적재 상태다. 임의의 목업을 운영 결과처럼 넣지 않으며, 정식 Loader 또는 팀의 검증된 초기 적재 절차로 스냅샷을 준비한다.

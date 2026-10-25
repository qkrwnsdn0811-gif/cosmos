# NEXUS GCP CI/CD — 설계 (2026-10-26)

`NEXUS` 브랜치에 push하면 GitHub Actions가 테스트하고, 이미지를 Artifact Registry에 올린 뒤, 시연 서버
(`https://34-64-222-128.sslip.io`, GCP 서울 VM)에 배포한다. 지금은 [deploy-update.md](../../nexus/deploy-update.md)를
사람이 서버에서 따라 해야 코드가 올라간다. 11-03 발표 전까지 커밋이 서버에 자동으로 반영되게 하는 것이 목표다.

PARTION-LAB(AWS)에서 쓴 방식 — `*-ci` 성공 뒤 `deploy-*`가 레지스트리(ECR)에 이미지를 올리고 서버(ECS)를 바꾸는 흐름 — 을
GCP로 옮긴다. 서버는 새로 만들지 않고 지금 VM과 `deploy/scripts/deploy.sh`를 그대로 쓴다.

## 정한 것

| 항목 | 값 |
|---|---|
| 목적 | 해커톤 시연용 단기 운영. 비용 최소, 발표 전 위험 최소 (사용자 선택) |
| 방식 | GitHub Actions 빌드 → Artifact Registry → 기존 VM에서 `deploy.sh` (사용자 선택. Cloud Run 이전과 VM 직접 빌드는 뺐다) |
| 트리거 | `NEXUS` push 중 `BackEnd/**`, `FrontEnd/**`, `deploy/compose.yml`, `deploy/scripts/**`, `.github/workflows/nexus.yml` 변경. 수동 실행(`workflow_dispatch`). `NEXUS` 대상 PR은 ci만 |
| GCP 인증 | Workload Identity Federation. 키 파일·GitHub Secrets 없음. `qkrwnsdn0811-gif/cosmos`의 `refs/heads/NEXUS` 토큰만 받는다 |
| 서버 접속 | `gcloud compute ssh --tunnel-through-iap`. 22번 포트는 IAP 대역(35.235.240.0/20)에만 연다 |
| 이미지 태그 | 커밋 SHA 앞 12자리. Jenkins 태그(`<sha12>-<빌드번호>`)와 섞이지 않게 `gh-` 접두사를 붙인다: `gh-<sha12>` |
| 빼는 것 | DB 덤프 복원(수동 절차 유지), 운영 비밀값 위치 변경(`/etc/cosmos/app.env` 유지), nexus 파이프라인, Jenkins·`Jenkinsfile` |

`workflow_run`으로 ci와 deploy를 잇는 PARTION 방식은 쓰지 않는다. `workflow_run`은 기본 브랜치(`main`)의 워크플로 파일로만
동작하는데 배포 브랜치는 `NEXUS`다. 한 워크플로 안에서 `needs`로 잇는다.

## 1. 워크플로 `.github/workflows/nexus.yml`

| 잡 | 조건 | 하는 일 |
|---|---|---|
| `frontend` | 항상 | Node 24, `npm ci` → `npm run lint` → `npm test` → `npm run build` (`FrontEnd/`) |
| `backend` | 항상 | Java 21(temurin), 서비스 컨테이너 Postgres 17·Redis 7, `./gradlew --no-daemon test` (`BackEnd/`). 실패 시 테스트 리포트를 아티팩트로 |
| `deploy` | `frontend`·`backend` 성공, 그리고 push 또는 수동 실행 | GCP 인증 → 두 이미지 빌드·푸시 → IAP SSH로 VM에서 `deploy-from-registry.sh` |

- 백엔드 테스트 환경 값은 `deploy/compose.ci.yml`과 같다(`DB_URL`, `DB_USERNAME`, `DB_PASSWORD`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD=''`).
- 이미지 빌드는 지금 Dockerfile 그대로 `--platform linux/amd64`, `org.opencontainers.image.revision` 라벨(`build-images.sh`와 같다), GHA 빌드 캐시.
- `concurrency: nexus-deploy`(진행 중인 배포는 끊지 않음). 대기 중인 오래된 실행은 최신 실행으로 바뀐다 — `Jenkinsfile`의
  "오래된 빌드가 최신 커밋을 덮지 않기"와 같은 목적이다.
- GCP 값(프로젝트, 리전, VM 이름·존, WIF 공급자, 서비스 계정)은 비밀이 아니므로 워크플로 `env`에 적는다.

## 2. VM 쪽 `deploy/scripts/deploy-from-registry.sh`

`deploy.sh`는 VM 로컬의 `cosmos-backend:<태그>`·`cosmos-frontend:<태그>` 이미지와 같은 체크아웃의 `deploy/compose.yml`을 쓴다.
새 스크립트는 그 준비만 하고 `deploy.sh`에 넘긴다.

1. 인자: 커밋 SHA(40자), 레지스트리 경로. 형식이 틀리면 종료.
2. 배포용 체크아웃(VM에서 확인한 경로)에서 `git fetch origin NEXUS` 후 그 SHA로 `checkout --detach`. 원격 `NEXUS`의 조상이 아니면 종료.
3. `docker pull` 두 이미지 → `docker tag`로 `cosmos-backend:gh-<sha12>`, `cosmos-frontend:gh-<sha12>`.
4. `COSMOS_SMOKE_URL=https://34-64-222-128.sslip.io bash deploy/scripts/deploy.sh gh-<sha12>`.

헬스체크, HTTPS·gzip·HTTP/2 스모크 테스트, 실패 시 직전 릴리스 복구, 이미지 정리는 `deploy.sh`가 이미 한다. `deploy.sh`와
`deploy/compose.yml`은 고치지 않는다. 레지스트리에서 받은 원본 태그(`asia-northeast3-docker.pkg.dev/...`)는 `deploy.sh`의
정리 대상이 아니므로 새 스크립트가 배포 뒤 현재·직전 태그를 뺀 나머지를 지운다.

VM이 레지스트리에서 pull하는 권한은 VM 서비스 계정에 `artifactregistry.reader`를 주거나, Actions가 짧은 수명의 접근 토큰을
SSH로 넘겨 `docker login`한다. VM의 서비스 계정·액세스 범위를 보고 정한다.

## 3. GCP 리소스

| 리소스 | 값 |
|---|---|
| Artifact Registry | 저장소 `cosmos` (Docker, `asia-northeast3`) |
| 서비스 계정 | `github-deployer` — 레지스트리 `writer`(저장소 한정), VM에 `compute.instanceAdmin.v1`(인스턴스 한정), `iap.tunnelResourceAccessor`, VM 서비스 계정에 `iam.serviceAccountUser` |
| WIF | 풀 `github`, 공급자 `cosmos-nexus`. 조건 `assertion.repository == 'qkrwnsdn0811-gif/cosmos' && assertion.ref == 'refs/heads/NEXUS'` |
| 방화벽 | `allow-iap-ssh`: 35.235.240.0/20 → tcp:22, VM 네트워크 태그 한정 |
| 고정 IP | VM 외부 IP가 임시 IP면 지금 IP(34.64.222.128)를 고정 IP로 승격. 바뀌면 `sslip.io` 주소·인증서·CORS가 함께 깨진다 |

## 4. 확인 방법

1. 수동 실행으로 첫 배포. 세 잡 모두 성공.
2. `https://34-64-222-128.sslip.io/healthz`가 `UP`, VM의 `/opt/cosmos/releases/current`가 `gh-<sha12>`.
3. [demo-scenario.md](../../nexus/demo-scenario.md)의 시연 뉴스 주소(`/?event=b8b8e847-...`)와 관계 은하·기업 탭이 열린다.
4. 그 뒤 `NEXUS`에 코드 커밋을 push해 자동 배포가 한 번 더 도는지 본다.

## VM에서 확인할 것 (로그인 뒤)

- VM 이름·존·크기, 켜져 있는지(10-26 접속 시 응답 없음), 외부 IP 종류
- 배포용 저장소 체크아웃 경로와 그 원격 주소, `deploy.sh`로 배포했는지(`/opt/cosmos/releases/current`)
- 환경 파일(`/etc/cosmos/app.env` 또는 `/opt/cosmos/releases/app.env`), 호스트 Nginx·인증서
- SSH 사용자의 docker 실행 권한, VM 서비스 계정과 액세스 범위

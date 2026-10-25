# NEXUS GCP CI/CD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `NEXUS` push가 테스트 → Artifact Registry → 새 GCP VM의 `deploy.sh`까지 자동으로 이어지게 한다.

**Architecture:** GitHub Actions 한 워크플로(`frontend`·`backend` → `deploy`). `deploy`는 WIF로 GCP에 인증해 이미지를 푸시하고,
IAP SSH로 VM의 `deploy-from-registry.sh`를 부른다. 그 스크립트가 이미지를 받아 기존 `deploy.sh`(헬스체크·롤백)에 넘긴다.
VM과 GCP 리소스는 `deploy/gcp/provision.sh`가 만들고, 서버 구성은 기존 `bootstrap-host.sh`·`enable-https.sh`를 도메인 변수로 쓴다.

**Tech Stack:** GitHub Actions, gcloud CLI 590, Artifact Registry, Compute Engine(Ubuntu 24.04), Docker Compose, bash, Let's Encrypt(certbot)

**Spec:** [docs/superpowers/specs/2026-10-26-nexus-gcp-cicd-design.md](../specs/2026-10-26-nexus-gcp-cicd-design.md)

## Global Constraints

- 프로젝트 `cosmos-nexus-1026`(이미 쓰이면 `cosmos-nexus-1026-<2자리>`), 결제 계정 `01AE03-48E726-85FD06`, 리전 `asia-northeast3`, 존 `asia-northeast3-a`
- VM `nexus-vm` e2-standard-2, Ubuntu 24.04 LTS, 30 GB, 고정 IP `nexus-ip`, 네트워크 태그 `cosmos-web`, `enable-oslogin=TRUE`
- 이미지 `asia-northeast3-docker.pkg.dev/<프로젝트>/cosmos/cosmos-{backend,frontend}:gh-<sha12>`
- WIF 조건 `assertion.repository == 'qkrwnsdn0811-gif/cosmos' && assertion.ref == 'refs/heads/NEXUS'`
- `deploy.sh`, `deploy/compose.yml`, `deploy/nginx/cosmos.conf`, `Jenkinsfile`은 고치지 않는다
- `COSMOS_DOMAIN` 기본값은 `j15c205.p.ssafy.io` — 기본값으로 돌리면 EC2용 동작이 지금과 같아야 한다
- 비밀값(DB·Redis·JWT·Gmail 앱 비밀번호)은 저장소·로그·채팅에 나오지 않는다
- 셸 스크립트는 기존 스크립트처럼 `#!/usr/bin/env bash` + `set -Eeuo pipefail`, 4칸 들여쓰기, 태그 정규식 `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}$`
- Actions 버전: `actions/checkout@v7`, `actions/setup-node@v7`, `actions/setup-java@v6`, `actions/upload-artifact@v7`, `gradle/actions/setup-gradle@v6`, `google-github-actions/auth@v3`, `google-github-actions/setup-gcloud@v3`, `docker/setup-buildx-action@v4`, `docker/login-action@v4`, `docker/build-push-action@v7`

## Review Focus

- `NEXUS`가 force-push돼 예전 SHA를 다시 실행(re-run)하면 그 SHA는 배포되지 않아야 한다 → Task 2 `not-on-nexus` 시나리오
- 레지스트리 pull이 실패하면 `deploy.sh`를 부르지 않고 지금 서비스를 그대로 둬야 한다 → Task 2 `pull-failure` 시나리오
- `deploy.sh`가 실패(롤백)하면 Actions 잡도 실패로 보여야 한다 → Task 2 `deploy-failure` 시나리오(종료 코드 전달)
- VM이 재부팅되면 같은 주소로 서비스가 돌아와야 한다 → Task 4 재부팅 확인 단계
- 인증서가 90일 뒤에도 갱신돼야 한다 → Task 4 `certbot renew --dry-run` 단계

---

### Task 1: 서버 구성 스크립트의 도메인 변수화

**Files:**
- Modify: `deploy/scripts/bootstrap-host.sh` (PUBLIC_URL 줄, ACME Nginx heredoc)
- Modify: `deploy/scripts/enable-https.sh` (인증서 확인 줄, `cosmos.conf` 설치 줄)

**Interfaces:**
- Produces: 두 스크립트가 환경변수 `COSMOS_DOMAIN`(기본 `j15c205.p.ssafy.io`)을 받는다. Task 4가 `COSMOS_DOMAIN=<IP>.sslip.io`로 부른다.

- [ ] **Step 1:** 두 스크립트 맨 위(루트 확인 뒤)에 `domain="${COSMOS_DOMAIN:-j15c205.p.ssafy.io}"`와 형식 확인(`^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$`, 틀리면 `exit 2`)을 넣는다.
- [ ] **Step 2:** `bootstrap-host.sh`: `PUBLIC_URL=https://%s`에 `$domain`, ACME heredoc을 따옴표 없는 heredoc으로 바꿔 `server_name ${domain};`. heredoc 안에 다른 `$`가 없음을 확인한다.
- [ ] **Step 3:** `enable-https.sh`: 인증서 두 경로를 `/etc/letsencrypt/live/$domain/...`로, `install ... cosmos.conf` 대신 `sed "s/j15c205\.p\.ssafy\.io/$domain/g" deploy/nginx/cosmos.conf > /etc/nginx/sites-available/cosmos` 후 `chmod 0644`. 주석: `cosmos.conf`의 SSAFY 도메인이 바꿀 자리라는 것.
- [ ] **Step 4:** 확인 — `bash -n` 두 파일 통과. `COSMOS_DOMAIN`을 비운 채 `grep`으로 기본값 경로가 이전과 같은지 본다. ShellCheck는 Task 4에서 VM(bootstrap이 설치)에서 돌린다.
- [ ] **Step 5:** 커밋 `feat(deploy): 서버 구성 스크립트가 COSMOS_DOMAIN으로 도메인을 받게`

### Task 2: `deploy-from-registry.sh`와 테스트

**Files:**
- Create: `deploy/scripts/deploy-from-registry.sh`
- Create: `deploy/tests/deploy-from-registry-test.sh`

**Interfaces:**
- Produces: `deploy-from-registry.sh <40자 SHA> <레지스트리 경로>` — `cosmos-ci`로 VM 체크아웃(`/opt/cosmos/checkouts/cosmos`)에서 실행. 종료 코드: 0 성공, 2 인자 형식 오류, 1 그 밖(조상 아님·pull 실패), `deploy.sh`의 코드는 그대로 전달.
- 환경변수: `COSMOS_ENV_FILE`(기본 `/etc/cosmos/app.env`, `PUBLIC_URL`을 읽어 `COSMOS_SMOKE_URL`로), `COSMOS_METADATA_TOKEN_URL`(기본 `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token`, 테스트용)

- [ ] **Step 1: 실패하는 테스트 작성** — `deploy-test.sh`와 같은 구조(임시 디렉터리, `PATH` 앞에 가짜 `git`·`docker`·`curl`, 가짜 `deploy/scripts/deploy.sh`가 인자와 `COSMOS_SMOKE_URL`을 기록, 호출 로그 `FAKE_CALLS`). 시나리오와 기대값:
  - `success`: 호출 순서가 `git fetch --quiet origin NEXUS` → `git merge-base --is-ancestor <sha> FETCH_HEAD` → `git checkout --quiet --detach <sha>` → `curl`(헤더 `Metadata-Flavor: Google`) → `docker login -u oauth2accesstoken --password-stdin asia-northeast3-docker.pkg.dev`(표준입력이 가짜 토큰 `ya29.test`) → `docker pull`·`docker tag`·`docker image rm` 각 두 번(backend, frontend; tag 대상 `cosmos-backend:gh-0123456789ab`) → `docker logout asia-northeast3-docker.pkg.dev` → `deploy.sh gh-0123456789ab`, `COSMOS_SMOKE_URL=https://example.sslip.io`. 종료 0.
  - `bad-sha`(`0123`)와 `bad-registry`(`docker.io/x`): 종료 2, `git`·`docker` 호출 없음.
  - `not-on-nexus`(가짜 `merge-base`가 1): 종료 1, `checkout`·`docker pull`·`deploy.sh` 호출 없음.
  - `pull-failure`(frontend pull이 1): 종료 1, `deploy.sh` 호출 없음, `docker logout`은 호출됨.
  - `deploy-failure`(가짜 `deploy.sh`가 17): 종료 17.
- [ ] **Step 2:** `bash deploy/tests/deploy-from-registry-test.sh` → 스크립트가 없어 FAIL.
- [ ] **Step 3: 구현** — 본문은 `main() { ... }`, 마지막 줄 `main "$@"; exit $?`(실행 중 `git checkout`이 이 파일을 바꿔도 이미 읽힌 본문으로 끝나게; 주석으로 이유를 적는다). 레지스트리 정규식 `^[a-z0-9-]+-docker\.pkg\.dev/[a-z0-9-]+/[a-z0-9-]+$`, SHA `^[0-9a-f]{40}$`. 토큰은 `sed -n 's/.*"access_token" *: *"\([^"]*\)".*/\1/p'`로 뽑는다(파이썬 의존 없음). pull 실패 시에도 `logout`이 되도록 `trap ... EXIT` 대신 pull 블록 뒤에서 결과를 보고 로그아웃한다.
- [ ] **Step 4:** 새 테스트와 기존 `bash deploy/tests/deploy-test.sh` 둘 다 통과.
- [ ] **Step 5:** 커밋 `feat(deploy): Artifact Registry 이미지를 받아 deploy.sh로 넘기는 VM 배포 스크립트`

### Task 3: GCP 리소스 — `deploy/gcp/provision.sh` 작성과 실행

**Files:**
- Create: `deploy/gcp/provision.sh`

**Interfaces:**
- Consumes: gcloud 로그인 계정 `gusals000914@gmail.com`(조직 `510496040735`의 프로젝트 생성·결제 권한 확인됨)
- Produces: 마지막에 출력하는 값 — `PROJECT_ID`, `PROJECT_NUMBER`, `WIF_PROVIDER`(`projects/<번호>/locations/global/workloadIdentityPools/github/providers/cosmos-nexus`), `DEPLOYER_SA`(`github-deployer@<프로젝트>.iam.gserviceaccount.com`), `REGISTRY`, `DUMP_BUCKET`(`gs://<프로젝트>-dumps`), `STATIC_IP`, `DEMO_URL`(`https://<IP의 .을 ->.sslip.io`). Task 4·5가 쓴다.

- [ ] **Step 1:** 스크립트 작성. 변수 `PROJECT_ID`(기본 `cosmos-nexus-1026`), `BILLING_ACCOUNT`, `REGION`, `ZONE`, `GITHUB_REPOSITORY`(기본 `qkrwnsdn0811-gif/cosmos`). 각 리소스는 `describe`가 성공하면 건너뛴다. 순서: 프로젝트 생성·결제 연결 → API 활성화 → AR 저장소 → 버킷(`--uniform-bucket-level-access --public-access-prevention`) → 서비스 계정 둘과 스펙 1절 표의 역할 → WIF 풀·공급자·`workloadIdentityUser`(`principalSet://iam.googleapis.com/projects/<번호>/locations/global/workloadIdentityPools/github/attribute.repository/<저장소>`) → 방화벽 둘, `default-allow-ssh`·`default-allow-rdp` 삭제 → 고정 IP → VM(`--image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud --boot-disk-size=30GB --boot-disk-type=pd-balanced --scopes=cloud-platform --metadata=enable-oslogin=TRUE`) → 값 출력.
- [ ] **Step 2:** `bash -n` 통과 후 실행. 프로젝트 ID가 이미 쓰이면 접미사로 다시 실행.
- [ ] **Step 3:** 확인 — `gcloud compute instances describe nexus-vm`이 `RUNNING`이고 외부 IP가 `nexus-ip`, `gcloud iam workload-identity-pools providers describe cosmos-nexus`의 조건이 Global Constraints와 같다, `gcloud compute ssh nexus-vm --tunnel-through-iap --command 'uname -a'` 성공.
- [ ] **Step 4:** 커밋 `feat(gcp): NEXUS 시연 서버 GCP 리소스 생성 스크립트` (출력 값은 커밋하지 않는다 — Task 5의 워크플로 `env`에 들어간다)

### Task 4: VM 서버 구성과 HTTPS

**Files:** 없음(VM 작업). 결과는 Task 6 문서에 적는다.

**Interfaces:**
- Consumes: Task 1의 `COSMOS_DOMAIN`, Task 3의 `DEMO_URL`·VM
- Produces: VM의 `/opt/cosmos/checkouts/cosmos`(이 브랜치, `cosmos-ci` 소유), `/etc/cosmos/app.env`, 인증서, 호스트 Nginx

- [ ] **Step 1:** 브랜치 `ci/nexus-gcp-deploy`를 origin에 push(이 브랜치 push는 워크플로를 부르지 않는다). IAP SSH로 저장소를 `/opt/cosmos/checkouts/cosmos`에 그 브랜치로 클론. `COSMOS_DOMAIN=<주소> sudo -E bash deploy/scripts/bootstrap-host.sh` → `chown -R cosmos-ci:cosmos-ci` 체크아웃.
- [ ] **Step 2:** `app.env`에 `MAIL_USERNAME=unset@example.com`, `MAIL_PASSWORD=unset`을 더한다(값을 출력하지 않는 `tee -a`). 
- [ ] **Step 3:** **사용자에게 Let's Encrypt 약관 동의와 등록 이메일을 확인받은 뒤** `certbot certonly --webroot -w /var/www/cosmos-acme -d <주소> --agree-tos -m <이메일> -n` → `COSMOS_DOMAIN=<주소> sudo -E bash deploy/scripts/enable-https.sh`.
- [ ] **Step 4:** 확인 — `curl -sI https://<주소>/`가 HTTP/2 응답(앱 전이라 502여도 TLS는 정상), `sudo certbot renew --dry-run` 성공, VM에서 `shellcheck deploy/scripts/*.sh deploy/tests/*.sh deploy/gcp/provision.sh` 경고 없음, `bash deploy/tests/deploy-from-registry-test.sh`·`deploy-test.sh` 통과(VM의 bash 5).

### Task 5: 워크플로 `.github/workflows/nexus.yml`

**Files:**
- Create: `.github/workflows/nexus.yml`

**Interfaces:**
- Consumes: Task 3 출력 값(`env`에), Task 2 스크립트 경로와 인자 순서

- [ ] **Step 1:** 스펙 3절 표대로 작성. 트리거·`paths`는 스펙 '정한 것' 표 그대로(`pull_request`도 같은 `paths`). 최상위 `permissions: contents: read`, `deploy`만 `id-token: write`. `deploy` 조건 `github.event_name != 'pull_request' && github.ref == 'refs/heads/NEXUS'`, `concurrency: { group: nexus-deploy, cancel-in-progress: false }`, `environment: { name: nexus-demo, url: <DEMO_URL> }`.
- [ ] **Step 2:** `backend` 서비스 컨테이너: `postgres:17-alpine`(`POSTGRES_DB/USER=cosmos_test`, `POSTGRES_PASSWORD=ci-test-only`, `pg_isready` 헬스체크, 5432 공개), `redis:7-alpine`(6379, `redis-cli ping`). 테스트 env는 `localhost`로 `compose.ci.yml` 값과 같게. 실패 시 `BackEnd/build/reports/tests/test`를 `upload-artifact`.
- [ ] **Step 3:** `deploy` 단계: `auth@v3`(`workload_identity_provider`, `service_account`, `token_format: access_token`) → `login-action@v4`(`asia-northeast3-docker.pkg.dev`, `oauth2accesstoken`, 토큰) → buildx → `build-push-action@v7` 두 번(context `BackEnd`/`FrontEnd`, `platforms: linux/amd64`, 라벨 `org.opencontainers.image.revision=${{ github.sha }}`, `cache-from/to: type=gha,scope=<앱>`) → `setup-gcloud@v3` → `gcloud compute ssh nexus-vm --zone … --project … --tunnel-through-iap --quiet --command "sudo -u cosmos-ci bash /opt/cosmos/checkouts/cosmos/deploy/scripts/deploy-from-registry.sh ${{ github.sha }} $REGISTRY"` → `curl -fsS <DEMO_URL>/healthz | grep -q '"status":"UP"'`.
- [ ] **Step 4:** 확인 — VM에서 `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7` 경고 없음.
- [ ] **Step 5:** 커밋 `ci: NEXUS push를 GCP 시연 서버에 배포하는 GitHub Actions`

### Task 6: 운영 문서

**Files:**
- Create: `deploy/gcp/README.md`

- [ ] **Step 1:** 스펙 6절 항목대로: 리소스 표(Task 3 값), CI/CD 흐름(PARTION과 다른 점 한 줄 — `workflow_run` 대신 `needs`), 처음 구성 순서(Task 3·4), 덤프 복원(스펙 5절 명령), Gmail 앱 비밀번호 넣기(사용자가 직접 `sudoedit /etc/cosmos/app.env` 후 `deploy-from-registry.sh` 대신 현재 태그로 `deploy.sh` 재실행), 되돌리기(이전 태그로 `deploy.sh`), 시연 뒤 정리(`gcloud projects delete <프로젝트>`). 저장소 문서 말투(한국어, 표, 짧은 문장)를 따른다.
- [ ] **Step 2:** `34-64-222-128.sslip.io`가 적힌 문서(`grep -rn`으로 찾는다, `docs/nexus/deploy-update.md` 등)를 Task 3의 `DEMO_URL`로 바꾼다. `deploy-update.md`의 서버 갱신 순서는 CI/CD가 대신한다는 한 줄과 `deploy/gcp/README.md` 링크를 더한다.
- [ ] **Step 3:** 커밋 `docs(gcp): NEXUS 시연 서버 운영 안내와 새 시연 주소`

### Task 7: 합치기, 첫 배포, 데이터

**Files:** 없음

- [ ] **Step 1:** 브랜치 push, `NEXUS` 대상 PR 생성 → PR에서 `frontend`·`backend`만 돌고 `deploy`는 건너뛰는지 확인.
- [ ] **Step 2:** **사용자 확인 뒤** PR 병합 → `NEXUS` push로 세 잡 성공, `<DEMO_URL>/healthz` `UP`, VM `/opt/cosmos/releases/current` = `gh-<sha12>`.
- [ ] **Step 3:** 사용자가 덤프를 버킷에 올리면 VM에서 복원(스펙 5절) → `/api/events?valueChain=SEMICONDUCTOR&size=1`에 `newsType`, 시연 뉴스 주소가 열린다.
- [ ] **Step 4:** VM `sudo reboot` 뒤 같은 주소에서 `/healthz` `UP`.
- [ ] **Step 5:** 그 뒤 `NEXUS`에 처음 올라오는 문서만 바뀐 커밋에서 `nexus` 워크플로가 돌지 않았는지 Actions 목록으로 확인한다(직접 push하지 않는다).

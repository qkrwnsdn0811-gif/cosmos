# NEXUS GCP CI/CD — 설계 (2026-10-26)

`NEXUS` 브랜치에 push하면 GitHub Actions가 테스트하고, 이미지를 Artifact Registry에 올린 뒤, GCP 서울 VM의 시연 서버에
배포한다. 11-03 발표 전까지 커밋이 서버에 자동으로 반영되게 하는 것이 목표다.

PARTION-LAB(AWS)에서 쓴 방식 — `*-ci` 성공 뒤 `deploy-*`가 레지스트리(ECR)에 이미지를 올리고 서버(ECS)를 바꾸는 흐름 — 을
GCP로 옮긴다. 서버 쪽 배포는 이미 있는 `deploy/scripts/deploy.sh`(잠금·헬스체크·스모크 테스트·직전 릴리스 복구)를 그대로 쓴다.

## 배경: 이전 시연 서버는 없다

예전 시연 주소 `https://34-64-222-128.sslip.io`의 VM은 2026-10-26 04:18(KST) `gusals000914@gmail.com` 계정으로 삭제됐다
(`nexus-hackathon-1021`/`nexus-vm`, 디스크 포함). 고정 IP `nexus-ip`도 반납했고 04:22~04:32에 그 계정의 프로젝트 5개 모두
삭제를 요청했다(이 Mac의 `~/.config/gcloud/logs/2026.10.26/`). VM·디스크·IP는 되돌릴 수 없으므로 새 프로젝트에 서버를
새로 만든다(사용자 선택). 시연 주소는 새 IP의 `sslip.io` 주소로 바뀐다.

## 정한 것

| 항목 | 값 |
|---|---|
| 목적 | 해커톤 시연용 단기 운영. 비용 최소, 발표 전 위험 최소 (사용자 선택) |
| 방식 | GitHub Actions 빌드 → Artifact Registry → VM에서 `deploy.sh` (사용자 선택. Cloud Run 이전과 VM 직접 빌드는 뺐다) |
| GCP | 새 프로젝트 `cosmos-nexus-1026`(이미 있으면 접미사), 결제 계정 '내 결제 계정', 리전 `asia-northeast3`, 존 `asia-northeast3-a` |
| VM | `nexus-vm`, e2-standard-2(2 vCPU·8 GB), Ubuntu 24.04 LTS, 30 GB, 고정 IP `nexus-ip`. compose 메모리 상한 합이 3.7 GB라 4 GB는 빠듯하다 (사용자 선택) |
| 시연 주소 | `https://<고정 IP를 -로 이은 값>.sslip.io` (Let's Encrypt) |
| 트리거 | `NEXUS` push 중 `BackEnd/**`, `FrontEnd/**`, `deploy/compose.yml`, `deploy/scripts/**`, `.github/workflows/nexus.yml` 변경. 수동 실행(`workflow_dispatch`). `NEXUS` 대상 PR은 테스트만 |
| GCP 인증 | Workload Identity Federation. 키 파일·GitHub Secrets 없음. `qkrwnsdn0811-gif/cosmos`의 `refs/heads/NEXUS` 토큰만 받는다 |
| 서버 접속 | OS Login + `gcloud compute ssh --tunnel-through-iap`. 22번은 IAP 대역(35.235.240.0/20)에만, 80·443은 공개 |
| 이미지 태그 | `gh-<커밋 SHA 앞 12자리>`. Jenkins 태그(`<sha12>-<빌드번호>`)와 섞이지 않는다 |
| 운영 비밀값 | `bootstrap-host.sh`가 VM 안에서 DB·Redis·JWT 값을 난수로 만든다(`/etc/cosmos/app.env`). Gmail 앱 비밀번호는 사용자가 직접 넣고, 그 전에는 자리표시 값이라 가입 메일만 실패한다 |
| DB 데이터 | `cosmos-nexus-2026-10-26.dump`(개발 PC)를 GCS 버킷에 올리면 VM에서 한 번 복원한다. CI/CD는 DB를 건드리지 않는다 |
| 빼는 것 | Jenkins, nexus 파이프라인, 메일 계정 값 자동 주입, 배포 뒤 DB 백업 자동화 |

`workflow_run`으로 ci와 deploy를 잇는 PARTION 방식은 쓰지 않는다. `workflow_run`은 기본 브랜치(`main`)의 워크플로 파일로만
동작하는데 배포 브랜치는 `NEXUS`다. 한 워크플로 안에서 `needs`로 잇는다.

## 1. GCP 리소스 — `deploy/gcp/provision.sh`

다시 실행해도 되는(있으면 건너뛰는) gcloud 스크립트로 남긴다. 사람이 이 Mac에서 한 번 실행한다.

| 리소스 | 값 |
|---|---|
| API | compute, artifactregistry, iam, iamcredentials, sts, iap, storage |
| Artifact Registry | Docker 저장소 `cosmos` (`asia-northeast3`) |
| GCS 버킷 | `<프로젝트>-dumps` (서울, 균일 접근, 공개 차단). DB 덤프 전달용 |
| VM 서비스 계정 | `nexus-vm` — 레지스트리 `reader`(저장소 한정), 덤프 버킷 `objectViewer`(버킷 한정) |
| 배포 서비스 계정 | `github-deployer` — 레지스트리 `writer`(저장소 한정), 프로젝트 `compute.viewer`·`compute.osAdminLogin`·`iap.tunnelResourceAccessor`, `nexus-vm`에 `iam.serviceAccountUser` |
| WIF | 풀 `github`, 공급자 `cosmos-nexus`(issuer `https://token.actions.githubusercontent.com`). 조건 `assertion.repository == 'qkrwnsdn0811-gif/cosmos' && assertion.ref == 'refs/heads/NEXUS'`. `github-deployer`의 `workloadIdentityUser`는 이 저장소 주체 집합에만 |
| 방화벽 | `cosmos-web`(tcp:80,443, 전체), `cosmos-iap-ssh`(tcp:22, 35.235.240.0/20). 기본 네트워크의 `default-allow-ssh`·`default-allow-rdp`(0.0.0.0/0)는 지운다 |
| 고정 IP·VM | `nexus-ip`(서울 리전 외부 IP), `nexus-vm`(네트워크 태그 `cosmos-web`, `enable-oslogin=TRUE`, 서비스 계정 `nexus-vm`) |

## 2. 서버 구성 — 기존 스크립트 + 도메인 변수

`bootstrap-host.sh`·`enable-https.sh`는 SSAFY 도메인 `j15c205.p.ssafy.io`를 직접 적고 있다. 둘 다 `COSMOS_DOMAIN`
환경변수(기본값 `j15c205.p.ssafy.io`)를 받게 고친다. 기본값이 그대로라 EC2·Jenkins 쪽 동작은 바뀌지 않는다.

- `bootstrap-host.sh`: `PUBLIC_URL=https://$COSMOS_DOMAIN`, ACME용 Nginx `server_name $COSMOS_DOMAIN`.
- `enable-https.sh`: 인증서 경로를 `$COSMOS_DOMAIN`으로 확인하고, `deploy/nginx/cosmos.conf`의 `j15c205.p.ssafy.io`를
  `$COSMOS_DOMAIN`으로 바꿔 설치한다. `cosmos.conf` 자체는 고치지 않는다.

VM에서 한 번 하는 순서(사람이 IAP SSH로):

1. 공개 저장소를 `/opt/cosmos/checkouts/cosmos`에 클론(`cosmos-ci` 소유).
2. `COSMOS_DOMAIN=<주소> bash deploy/scripts/bootstrap-host.sh`.
3. `app.env`에 `MAIL_USERNAME`·`MAIL_PASSWORD` 자리표시 값을 더한다(`compose.yml`이 비어 있으면 거부한다).
4. `certbot certonly --webroot -w /var/www/cosmos-acme -d <주소>` — Let's Encrypt 약관 동의는 사용자 확인 뒤.
5. `COSMOS_DOMAIN=<주소> bash deploy/scripts/enable-https.sh`.

## 3. 워크플로 `.github/workflows/nexus.yml`

| 잡 | 조건 | 하는 일 |
|---|---|---|
| `frontend` | 항상 | Node 24, `npm ci` → `npm run lint` → `npm test` → `npm run build` (`FrontEnd/`) |
| `backend` | 항상 | Java 21(temurin), 서비스 컨테이너 Postgres 17·Redis 7, `./gradlew --no-daemon test` (`BackEnd/`). 실패 시 테스트 리포트를 아티팩트로 |
| `deploy` | `frontend`·`backend` 성공, `refs/heads/NEXUS`의 push 또는 수동 실행 | WIF 인증 → 두 이미지 빌드·푸시 → IAP SSH로 VM에서 `deploy-from-registry.sh` → 러너에서 `/healthz` 확인 |

- 백엔드 테스트 환경 값은 `deploy/compose.ci.yml`과 같다(`DB_URL`, `DB_USERNAME`, `DB_PASSWORD`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD=''`).
- 이미지는 지금 Dockerfile 그대로 `linux/amd64`, `org.opencontainers.image.revision` 라벨(`build-images.sh`와 같다), GHA 빌드 캐시.
- `concurrency: nexus-deploy`(진행 중인 배포는 끊지 않음). 대기 중인 오래된 실행은 최신 실행으로 바뀐다 — `Jenkinsfile`의
  "오래된 빌드가 최신 커밋을 덮지 않기"와 같은 목적이다.
- GCP 값(프로젝트, 리전, VM, WIF 공급자, 서비스 계정, 시연 주소)은 비밀이 아니므로 워크플로 `env`에 적는다.

## 4. VM 쪽 `deploy/scripts/deploy-from-registry.sh`

`cosmos-ci`로 실행한다(`sudo -u cosmos-ci`). 인자는 커밋 SHA(40자)와 레지스트리 경로.

1. 인자 형식 확인. 체크아웃에서 `git fetch origin NEXUS`, 그 SHA가 원격 `NEXUS`의 조상이 아니면 종료, `checkout --detach`.
   실행 중에 자기 파일이 바뀌므로 본문을 함수로 감싸 끝까지 먼저 읽힌 뒤 실행한다.
2. 메타데이터 서버의 VM 서비스 계정 토큰으로 `docker login` → 두 이미지 `pull` → `cosmos-{backend,frontend}:gh-<sha12>`로
   `tag` → 레지스트리 이름 태그는 지운다(이미지는 짧은 이름이 계속 가리킨다) → `docker logout`.
3. `COSMOS_SMOKE_URL=<app.env의 PUBLIC_URL> bash deploy/scripts/deploy.sh gh-<sha12>`.

헬스체크, HTTPS·gzip·HTTP/2 스모크 테스트, 실패 시 직전 릴리스 복구, 현재·직전 외 이미지 정리는 `deploy.sh`가 한다.
`deploy.sh`와 `deploy/compose.yml`은 고치지 않는다. `deploy/tests/`에 가짜 `git`·`docker`·`curl`로 이 스크립트를 확인하는
테스트를 `deploy-test.sh`와 같은 방식으로 더한다.

## 5. DB 덤프 복원 (한 번, 수동)

[deploy-update.md](../../nexus/deploy-update.md) 3절 ③과 같다. 버킷에서 VM으로 받아 `DROP/CREATE` 뒤 `pg_restore`, 백엔드
컨테이너를 다시 시작한다. 덤프에 Flyway 기록(V16)이 들어 있어 마이그레이션을 다시 돌리지 않는다.

## 6. 문서

- `deploy/gcp/README.md`: 리소스 표, 처음 구성 순서, 덤프 복원, 메일 비밀번호 넣기, 되돌리기, 시연 뒤 프로젝트 삭제.
- 시연 주소가 적힌 문서(`docs/nexus/deploy-update.md` 등)는 첫 배포 뒤 새 주소로 바꾼다.

## 7. 확인 방법

1. 이 브랜치의 변경을 `NEXUS`에 합치면(워크플로 파일 변경이라 트리거된다) 세 잡이 모두 성공한다.
2. `https://<주소>/healthz`가 `UP`, VM의 `/opt/cosmos/releases/current`가 `gh-<sha12>`.
3. 덤프 복원 뒤 [demo-scenario.md](../../nexus/demo-scenario.md)의 시연 뉴스(`/?event=b8b8e847-...`)·관계 은하·기업 탭이 열린다.
4. 그 뒤 `NEXUS`에 코드 커밋을 push해 자동 배포가 한 번 더 돈다. 문서만 바꾼 커밋은 배포하지 않는다.

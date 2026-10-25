# NEXUS 시연 서버 (GCP)

시연 주소: **https://34-158-212-210.sslip.io**

`main` 브랜치에 앱 코드가 push되면 GitHub Actions(`.github/workflows/nexus.yml`)가 테스트하고 이 서버에 배포한다.
설계는 [docs/superpowers/specs/2026-10-26-nexus-gcp-cicd-design.md](../../docs/superpowers/specs/2026-10-26-nexus-gcp-cicd-design.md).

## 흐름

| 단계 | 하는 일 |
|---|---|
| `frontend` | `FrontEnd/`에서 `npm ci` → `lint` → `test` → `build` |
| `backend` | `BackEnd/`에서 일회용 Postgres 17·Redis 7로 `./gradlew test` |
| `deploy` | WIF로 GCP 인증 → 두 이미지를 Artifact Registry에 `gh-<sha12>` 태그로 푸시 → IAP SSH로 VM의 `deploy/scripts/deploy-from-registry.sh` → `/healthz` 확인 |

- 배포하는 push: `main`의 `BackEnd/**`, `FrontEnd/**`, `deploy/compose.yml`, `deploy/scripts/**`, 워크플로 파일 변경. 문서만 바꾼 커밋은 배포하지 않는다.
- `main` 대상 PR은 `frontend`·`backend`만 돈다. Actions 화면의 **Run workflow**로 `main`을 수동 배포할 수 있다.
- 2026-10-26에 `NEXUS`를 `main`에 합치고 원격 `NEXUS`를 지워서, 배포 기준을 `NEXUS`에서 `main`으로 바꿨다.
- `main` 실행은 push 순서대로 하나씩 돈다. 예전 실행을 다시 돌려도(re-run) 지금 서버에 있는 것보다 오래된 커밋이면 배포하지 않고 넘어간다.
- 헬스체크·HTTPS 스모크 테스트·실패 시 직전 릴리스 복구는 기존 `deploy.sh`가 한다. 실패하면 Actions 잡이 빨간색이 되고 서버는 직전 버전으로 돌아가 있다.
- PARTION-LAB(AWS)과 달리 ci와 deploy를 `workflow_run` 대신 한 워크플로의 `needs`로 잇는다. 배포가 같은 커밋의 두 테스트를 기다린다.
- GitHub Secrets는 쓰지 않는다. GCP 인증은 Workload Identity Federation이고 이 저장소의 `refs/heads/main` 토큰만 받는다.

## 리소스

| 리소스 | 값 |
|---|---|
| 프로젝트 | `cosmos-nexus-1026` (번호 1043600593127), 결제 계정 '내 결제 계정' |
| VM | `nexus-vm` (`asia-northeast3-a`, e2-standard-2, Ubuntu 24.04, 30 GB, 삭제 보호 켬) |
| 고정 IP | `nexus-ip` = 34.158.212.210 |
| 레지스트리 | `asia-northeast3-docker.pkg.dev/cosmos-nexus-1026/cosmos` |
| 덤프 버킷 | `gs://cosmos-nexus-1026-dumps` |
| 서비스 계정 | `nexus-vm`(VM: 레지스트리 읽기·버킷 읽기), `github-deployer`(Actions: 레지스트리 쓰기·OS Login·IAP) |
| WIF | 풀 `github`, 공급자 `cosmos-nexus` |
| 방화벽 | 80·443 공개, 22는 IAP 대역(35.235.240.0/20)만 |

리소스는 [provision.sh](provision.sh)가 만든다. 다시 실행해도 있는 것은 건너뛴다.

## 서버에 들어가기

```bash
gcloud compute ssh nexus-vm --zone asia-northeast3-a --project cosmos-nexus-1026 --tunnel-through-iap
```

배포용 체크아웃은 `/opt/cosmos/checkouts/cosmos`(`cosmos-ci` 소유), 운영 환경 파일은 `/etc/cosmos/app.env`(`root:cosmos-ci`, 0640),
릴리스 기록은 `/opt/cosmos/releases/current`·`.previous`다.

## 처음 구성한 순서 (2026-10-26)

1. `bash deploy/gcp/provision.sh` (이 Mac, `gcloud` 로그인 계정)
2. VM에서 저장소를 `/opt/cosmos/checkouts/cosmos`에 클론하고
   `sudo COSMOS_DOMAIN=34-158-212-210.sslip.io bash deploy/scripts/bootstrap-host.sh` → 체크아웃을 `cosmos-ci` 소유로
3. `app.env`에 `MAIL_USERNAME`·`MAIL_PASSWORD` 자리표시 값 추가 (`compose.yml`이 빈 값을 거부한다)
4. `sudo certbot certonly --webroot -w /var/www/cosmos-acme -d 34-158-212-210.sslip.io --agree-tos --register-unsafely-without-email`
5. `sudo COSMOS_DOMAIN=34-158-212-210.sslip.io bash deploy/scripts/enable-https.sh`

인증서는 `certbot.timer`가 갱신한다. 등록 이메일이 없으므로 만료 알림 메일은 오지 않는다.

## DB 덤프 넣기 (한 번)

CI/CD는 DB를 건드리지 않는다. 첫 배포 직후 DB에는 Flyway가 만든 빈 테이블만 있다.

1. 개발 PC에서 [버킷](https://console.cloud.google.com/storage/browser/cosmos-nexus-1026-dumps?project=cosmos-nexus-1026)에
   `cosmos-nexus-2026-10-26.dump`를 올린다.
2. VM에서 복원한다. 몇 분 동안 화면이 오류를 낸다.

```bash
gcloud storage cp gs://cosmos-nexus-1026-dumps/cosmos-nexus-2026-10-26.dump /tmp/
PG=$(sudo docker ps -q --filter label=com.docker.compose.project=cosmos --filter label=com.docker.compose.service=postgres)
sudo docker exec "$PG" psql -U cosmos -d postgres -c "DROP DATABASE cosmos WITH (FORCE)" -c "CREATE DATABASE cosmos OWNER cosmos"
sudo docker exec -i "$PG" pg_restore -U cosmos -d cosmos --no-owner --exit-on-error < /tmp/cosmos-nexus-2026-10-26.dump
sudo docker restart "$(sudo docker ps -q --filter label=com.docker.compose.project=cosmos --filter label=com.docker.compose.service=backend)"
```

덤프에 Flyway 기록(V16)이 들어 있어 백엔드는 마이그레이션을 다시 돌리지 않는다. 확인은
[deploy-update.md](../../docs/nexus/deploy-update.md) 4절과 같다.

## 가입 인증 메일 켜기

`app.env`의 `MAIL_USERNAME`·`MAIL_PASSWORD`는 자리표시 값이라 가입 인증 코드 요청이 500으로 끝난다(가입 외 화면은 정상).
Gmail 2단계 인증을 켜고 만든 앱 비밀번호(16자)를 직접 넣는다. 값은 채팅·저장소에 남기지 않는다.

```bash
sudoedit /etc/cosmos/app.env    # MAIL_USERNAME=<Gmail 주소>, MAIL_PASSWORD=<앱 비밀번호>
sudo -u cosmos-ci bash -c 'cd /opt/cosmos/checkouts/cosmos && bash deploy/scripts/deploy.sh "$(cat /opt/cosmos/releases/current)"'
```

## 되돌리기

직전 릴리스 이미지는 서버에 남아 있다.

```bash
sudo -u cosmos-ci bash -c 'cd /opt/cosmos/checkouts/cosmos && bash deploy/scripts/deploy.sh "$(cat /opt/cosmos/releases/.previous)"'
```

이미지 롤백은 DB 마이그레이션을 되돌리지 않는다. 그다음 `main`에 push하면 다시 최신으로 배포된다.

## 시연 뒤 정리

VM은 하루 약 2,800원이다. 시연이 끝나면 프로젝트째 지운다(30일 안에는 `gcloud projects undelete`로 되살릴 수 있다).

```bash
gcloud projects delete cosmos-nexus-1026
```

지우기 전에 필요하면 DB를 덤프해 둔다(`deploy-update.md` 3절 ①).

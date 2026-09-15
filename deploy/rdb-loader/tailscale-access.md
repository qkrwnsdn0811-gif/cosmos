# 별도 Hadoop Master에서 Main PostgreSQL 접근

Main PostgreSQL과 Hadoop Master가 다른 호스트이면 `compose.override.yml`의
`RDB_LOADER_PG_BIND_ADDRESS`를 **Main의 실제 Tailscale IPv4 한 개**로 설정한다.
기본값은 같은 호스트용 `127.0.0.1`이다. `0.0.0.0` 또는 `::`는 사용하지 않는다.
override는 PostgreSQL만 사용하는 `rdb_loader` 게시용 bridge도 추가한다.
기존 `data` 내부 네트워크는 유지한다. 내부 네트워크에만 연결된 컨테이너는 Docker에서
포트 요청이 있어도 실제 listener/NAT가 생성되지 않으므로 게시용 bridge가 필요하다.

현재 운영 주소는 Main `100.69.73.112`, Master `100.117.115.44`이며 포트는 5432다.
`/etc/cosmos/app.env`에 다음 비밀정보가 아닌 설정을 보관한다.

```dotenv
RDB_LOADER_PG_BIND_ADDRESS=100.69.73.112
RDB_LOADER_PG_HOST_PORT=5432
```

`deploy/rdb-loader/compose.override.yml`을 `/etc/cosmos/rdb-loader.compose.yml`에
설치하면 변경된 `deploy/scripts/deploy.sh`가 이후 릴리스에서도 override를 함께 적용한다.
아직 이 배포 스크립트를 사용하지 않는 운영 job은 먼저 같은 override를 사용하도록 바꿔야 한다.

## 방화벽 범위

Main의 기존 tailscale0 전체 차단 규칙보다 앞에 다음 두 예외를 추가한다.

1. UFW: `100.117.115.44`에서 `tailscale0`으로 들어오는 `100.69.73.112:5432/TCP`만 허용.
2. `/usr/local/sbin/cosmos-tailscale-docker-guard`의 `COSMOS-TS-FWD4`:
   Master 원본 주소와 conntrack의 **변환 전 목적지 주소/포트**가 일치하는 DB 연결만 허용.

Docker는 게시 포트를 컨테이너 IP로 변환한 후 forwarding guard를 실행한다.
따라서 컨테이너 IP를 고정하거나 `--dport 5432`만 허용하지 않는다.
현재 구성을 위한 영구 guard 생성 규칙은 다음과 같다.

```bash
printf -- '-A %s -i tailscale0 -s 100.117.115.44/32 -p tcp --dport 5432 -m conntrack --ctstate NEW --ctorigdst 100.69.73.112 --ctorigdstport 5432 -j RETURN\n' "$v4_chain"
```

이를 기존 IPv4 `tailscale0` DROP 규칙 생성 앞에 추가하고, guard의 `--check`를 통과한 뒤 적용한다.
기존 SSH·Worker·IPv6 규칙 및 chain hook은 유지한다. 임시 `iptables -I`만 추가하면 guard
재실행 시 사라지므로 영구 스크립트에도 반드시 반영한다.

## 적용과 검증

설정 변경 전 `app.env`, guard 스크립트, UFW 규칙, 현재 Compose 파일을 root 전용 백업
디렉터리(`0700`)에 보관한다. 같은 릴리스의 `IMAGE_TAG`, 운영 env, 현재 release compose와
override를 사용해 **postgres 서비스만** `up -d --no-deps --wait`로 재조정한다.
애플리케이션이나 Docker daemon을 재시작할 필요는 없다.

다음을 확인한다.

- PostgreSQL healthy 및 기존 backend `/actuator/health`와 공개 `/healthz`가 UP.
- Docker 게시 주소는 `100.69.73.112:5432` 한 개이며 `0.0.0.0`/`::` 바인딩은 없음.
- Master에서 Main 5432 연결 성공, Worker에서 같은 연결 실패.
- guard를 다시 실행해도 같은 허용/차단 결과 유지.
- Loader env는 `PGHOST=100.69.73.112`, `PGPORT=5432`를 사용.

연결 성공은 인증·Flyway 적용·업무 데이터 발행 성공과 별도로 검증한다.
이후 Loader 전용 계정과 Flyway V5 적용, 격리된 스키마에서의 통합 테스트를 진행한다.

## 되돌리기

실패 시 backup의 guard와 UFW 규칙을 복구하고 guard/UFW를 재적용한다.
backup의 `app.env`를 복구하며 새로 설치한 override를 비활성화한 뒤,
동일 릴리스의 기본 Compose만으로 postgres를 다시 조정한다.
이 과정에서 PostgreSQL named volume을 제거하거나 `docker compose down`을 실행하지 않는다.

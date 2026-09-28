# 포팅 매뉴얼

> 기준: `dev` 브랜치 `3953408` 코드와 운영 서버 실측값 (2026-09-28 확인). 운영 중인 이미지 태그는 `395340848c9a-92`로 이 커밋과 같습니다.
> 비밀번호·API 키·토큰 값은 이 문서에 적지 않습니다. 변수 이름과 파일 위치만 기록합니다.

## 1. 시스템 구성

| 서버 | 호스트 | 역할 |
| --- | --- | --- |
| 웹 EC2 (SSAFY 기본) | `j15c205.p.ssafy.io` | 호스트 Nginx, Spring Boot, 프론트 Nginx, PostgreSQL, Redis, Jenkins (모두 Docker) |
| 데이터 Master EC2 (SSAFY 추가) | `j15c205a.p.ssafy.io` | HDFS NameNode·SecondaryNameNode, YARN ResourceManager, Spark Driver, Kafka, 뉴스·공시·주가 수집기, AI 분석·RDB Loader (systemd) |
| AWS Worker 1~3 | Tailscale `100.104.188.115`, `100.68.206.1`, `100.124.47.107` | HDFS DataNode, YARN NodeManager (`m7i-flex.large`, 데이터 EBS 200 GiB × 3) |

- 두 SSAFY EC2 모두 Ubuntu 24.04.4 LTS, 4 vCPU, 16 GiB RAM입니다.
- 서버 사이는 Tailscale 사설망으로 연결합니다. 웹 EC2 `100.69.73.112`, Master `100.117.115.44`.
- 데이터 흐름: 수집기 → Kafka `news.raw` → HDFS Parquet → AI 분석(Spark/Python) → RDB Loader → PostgreSQL → Spring Boot API → 웹 화면.
- 상세: [`docs/architecture.md`](../docs/architecture.md), [`docs/infrastructure-design.md`](../docs/infrastructure-design.md), [`docs/HADOOP_CLUSTER.md`](../docs/HADOOP_CLUSTER.md).

## 2. 사용 제품과 버전

### 2.1 웹 서비스 (웹 EC2)

| 구분 | 제품 | 버전 | 근거 |
| --- | --- | --- | --- |
| JVM | Eclipse Temurin (OpenJDK) | 21.0.12.1 LTS (이미지 `eclipse-temurin:21-jre-jammy`) | 운영 컨테이너 `java -version` |
| 백엔드 프레임워크 | Spring Boot | 4.1.1 (dependency-management 1.1.7) | `BackEnd/build.gradle` |
| 빌드 도구 | Gradle Wrapper | 9.7.1 | `BackEnd/gradle/wrapper/gradle-wrapper.properties` |
| 주요 라이브러리 | Spring Data JPA, Security, Validation, Redis, Mail, Actuator, Flyway, jjwt 0.12.6, Lombok, PostgreSQL JDBC | Spring Boot BOM 관리 | `BackEnd/build.gradle` |
| WAS | Spring Boot 내장 Tomcat | Spring Boot 4.1.1 포함 버전 | 별도 WAS 설치 없음 |
| 프론트엔드 | React 19.2 / TypeScript 5.9 / Vite 8 / Three.js 0.185 / react-three-fiber 9 / TanStack Query 5 / Zustand 5 / React Router 7 | `FrontEnd/package.json` (`package-lock.json`으로 고정) | |
| 프론트 빌드 | Node.js | 24 (이미지 `node:24-alpine`, `engines`는 `>=20`) | `FrontEnd/Dockerfile` |
| 웹서버 (호스트) | Nginx | 1.24.0 (Ubuntu 패키지) | `nginx -v` |
| 웹서버 (프론트 컨테이너) | Nginx | 1.30.5 (이미지 `nginx:stable-alpine`) | 컨테이너 `nginx -v` |
| DB | PostgreSQL | 17.11 (이미지 `postgres:17-alpine`) | `postgres --version` |
| 캐시·토큰 저장 | Redis | 7.4.11 (이미지 `redis:7-alpine`) | `redis-server --version` |
| 컨테이너 | Docker Engine / Docker Compose | 29.8.0 / v5.5.1 | `docker --version` |
| CI/CD | Jenkins | 2.568.3 (JDK 21, 이미지 `jenkins/jenkins:2.568.3-jdk21`), 플러그인 71개 | `deploy/jenkins/` |
| 인증서 | Certbot (Let's Encrypt) | 2.9.0 | `certbot --version` |
| 사설망 | Tailscale | 1.102.4 | `tailscale version` |

### 2.2 데이터·AI (Master EC2, AWS Worker)

| 구분 | 제품 | 버전 |
| --- | --- | --- |
| Hadoop (HDFS, YARN) | Apache Hadoop | 3.5.0, Java 17 (`/usr/lib/jvm/java-17-openjdk-amd64`) |
| Spark | Apache Spark | 4.2.0 (`spark-4.2.0-bin-hadoop3`), Scala 2.13.18, Java 21 |
| 메시지 큐 | Apache Kafka | 4.3.1 (Scala 2.13), KRaft 단일 노드 (`broker,controller`), `9092` |
| Python | CPython | 3.12.3 (Ubuntu 기본), 모듈마다 별도 venv |
| Node.js (국내 뉴스 추출) | Node.js | 22.23.2 (`~/news-kafka/runtime`에 포함, `engines`: `>=22.13.0 <25`) |
| AI 모델 | FinBERT (`ProsusAI/finbert`) + 사전 기반 NER | Hugging Face `transformers` 5.16.1, `torch` 2.6.0 |

Python 모듈별 주요 의존성 (버전은 각 `requirements*.txt`에 고정):

| 모듈 | 파일 | 주요 패키지 |
| --- | --- | --- |
| 뉴스 수집 | `Crawling/news/requirements.txt` | confluent-kafka 2.15.1, curl-cffi 0.16.3, beautifulsoup4 4.15.0, pyarrow 25.0.1 |
| 주가 수집 | `Crawling/prices/requirements.txt` | pykrx 1.2.8, finance-datareader 0.9.202, pandas 2.3.3, psycopg 3.3.5 |
| 문서 적재 | `Crawling/documents/requirements.txt` | pyarrow 25.0.1, psycopg 3.3.5 |
| DART·SEC 공시 | `Crawling/disclosures/{dart,sec}` | 표준 라이브러리만 사용 |
| GPU 감성 분석 | `AI/gpu_news/requirements.txt` | transformers 5.16.1, tokenizers 0.23.2, numpy 2.5.3, pyarrow 23.0.1 |
| 그래프·영향도 | `AI/graph/requirements-*.txt` | torch 2.6.0, torch-geometric 2.8.0.post1, lightgbm 4.7.0, scikit-learn 1.9.0, fastapi 0.141.1 |
| RDB Loader | `AI/rdb_loader/requirements.txt` | psycopg[binary] 3.x, pyarrow 18~23 |

### 2.3 개발 환경 (IDE)

| 도구 | 버전 |
| --- | --- |
| IntelliJ IDEA | 2026.1.4 (백엔드) |
| Visual Studio Code | 1.134.0 (프론트엔드·Python) |
| Git | 2.55.0 |
| 로컬 Node.js / npm | 24.18.0 / 11.16.0 |
| 로컬 JDK | 백엔드 빌드에는 JDK 21 필요 (Gradle toolchain이 21을 요구합니다) |

## 3. 클론 후 빌드

### 3.1 로컬 개발 실행

```bash
git clone https://lab.ssafy.com/s15-bigdata-dist-sub1/S15P21C205.git
cd S15P21C205

# 1) 로컬 DB·Redis (BackEnd/docker-compose.yml: postgres:17-alpine 5432, redis:7-alpine 6379)
docker compose -f BackEnd/docker-compose.yml up -d

# 2) 백엔드: .env.example 을 .env 로 복사해 JWT_SECRET(32바이트 이상), MAIL_USERNAME/MAIL_PASSWORD 작성
cd BackEnd && cp .env.example .env
SERVER_PORT=18081 ./gradlew bootRun      # Flyway가 V1~V9 스키마를 생성

# 3) 프론트엔드: /api 요청을 VITE_API_PROXY(기본 http://localhost:18081)로 프록시
cd FrontEnd && cp .env.example .env
npm ci && npm run dev                    # http://localhost:5173
```

- 로컬 기본값은 `application.yml`에 있습니다 (DB `jdbc:postgresql://localhost:5432/cosmos`, 계정 `cosmos`, Redis `localhost:6379`, CORS `http://localhost:5173`).
- `application.yml`에는 `server.port`가 없어 `SERVER_PORT`를 주지 않으면 8080으로 뜹니다. 이때는 `FrontEnd/.env`의 `VITE_API_PROXY`를 `http://localhost:8080`으로 바꿉니다.
- 로컬 DB는 비어 있으므로 화면에 데이터를 보려면 [DB 덤프](DB_DUMP.md)를 복원합니다.

### 3.2 운영 이미지 빌드 (CI와 동일)

```bash
bash deploy/scripts/ci.sh <test-tag>              # 프론트 build·lint, 백엔드 jar, 임시 PG17·Redis7로 테스트
bash deploy/scripts/build-images.sh <release-tag> # cosmos-backend:<tag>, cosmos-frontend:<tag>
```

- 백엔드 이미지: `BackEnd/Dockerfile` (빌드 `eclipse-temurin:21-jdk-jammy` → 실행 `eclipse-temurin:21-jre-jammy`, 포트 18081, 비루트 uid 10001).
- 프론트 이미지: `FrontEnd/Dockerfile` (빌드 `node:24-alpine` → 실행 `nginx:stable-alpine`, 포트 80).

## 4. 빌드·실행 환경 변수

### 4.1 웹 서비스 — `/etc/cosmos/app.env` (`root:cosmos-ci`, 0640)

| 변수 | 용도 | 비고 |
| --- | --- | --- |
| `PUBLIC_URL` | 공개 주소, 백엔드 `CORS_ALLOWED_ORIGINS`로 전달 | `https://j15c205.p.ssafy.io` |
| `DB_PASSWORD` | PostgreSQL `cosmos` 계정 비밀번호 | 최초 볼륨 생성 시 확정 |
| `REDIS_PASSWORD` | Redis `requirepass` | |
| `JWT_SECRET` | Access/Refresh 토큰 서명 키 | 32바이트 이상, 없으면 앱이 뜨지 않음 |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | 인증 메일 발송용 Gmail 계정·앱 비밀번호 | `deploy/compose.yml`에서 필수 |
| `RDB_LOADER_PG_BIND_ADDRESS`, `RDB_LOADER_PG_HOST_PORT` | Master의 Loader가 PostgreSQL에 접속하도록 Tailscale 주소에만 5432 공개 | 서버 `/etc/cosmos/rdb-loader.compose.yml` (원본 `deploy/rdb-loader/compose.override.yml`, 절차 `deploy/rdb-loader/tailscale-access.md`) |
| `IMAGE_TAG` | 배포할 이미지 태그 | 배포 스크립트가 전달, 직접 쓰지 않음 |

Compose가 백엔드 컨테이너에 고정으로 넣는 값: `SPRING_PROFILES_ACTIVE=prod`, `SERVER_PORT=18081`, `DB_URL=jdbc:postgresql://postgres:5432/cosmos`, `DB_USERNAME=cosmos`, `REDIS_HOST=redis`, `REDIS_PORT=16379`, `JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=65.0`, `TZ=UTC`.

백엔드 선택 변수 (기본값 있음): `MAIL_HOST`(smtp.gmail.com), `MAIL_PORT`(587), `JWT_ACCESS_TOKEN_VALIDITY`(PT30M), `JWT_REFRESH_TOKEN_VALIDITY`(P14D), `COOKIE_SECURE`, `COOKIE_SAME_SITE`.

프론트 빌드 변수: `VITE_API_BASE=/api` (`FrontEnd/Dockerfile`에 고정, 코드 기본값도 `/api`), 개발 서버 전용 `VITE_API_PROXY`. Vite가 번들에 값을 넣으므로 바꾸면 이미지를 다시 빌드합니다. `docs/CICD.md`에 나오는 `VITE_API_MODE`는 현재 코드에서 쓰지 않습니다.

Jenkins 비밀값: `/etc/cosmos/jenkins/` (`admin-user`, `admin-password`, `webhook-token`), 에이전트 비밀 `/etc/cosmos/agent.secret`. GitLab Deploy Token은 Jenkins Credentials `cosmos-git-read`에 저장합니다.

### 4.2 데이터 파이프라인 — Master `/etc/cosmos/*.env`

| 파일 | 서비스 | 주요 변수 |
| --- | --- | --- |
| `naver-news.env` | 네이버 뉴스 API 수집 | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `NAVER_DAILY_BUDGET`, `NAVER_POLL_SECONDS`, `KAFKA_BOOTSTRAP_SERVERS`, `NEWS_TOPIC` |
| `sec-incremental.env`, `sec-user-agent` | SEC EDGAR 증분 수집 | `SEC_EDGAR_USER_AGENT_FILE`(연락처 포함 User-Agent), `SEC_UNIVERSE`, `SEC_REQUEST_DELAY`, `SEC_HDFS_ROOT` |
| `stock-prices.env`, `stock-prices.pgpass` | 코스피·나스닥 일봉 | `STOCK_PRICE_HDFS_ROOT`, `STOCK_PRICE_PG_LOAD`, `STOCK_DATABASE_URL` |
| `news-analysis.env` | 뉴스 기업 추출 | `NEWS_ANALYSIS_RAW_GLOB`, `NEWS_ANALYSIS_OUTPUT_BASE`, `NEWS_ANALYSIS_MODEL_VERSION` |
| `news-enrich.env`, `news-enrich.json` | FinBERT 감성 분석 | `NEWS_ENRICH_BUNDLE`, `NEWS_ENRICH_DEVICE`(cpu), `NEWS_ENRICH_BATCH_SIZE` |
| `sentiment-loader.env`, `relation-loader.env` | 분석 결과 → PostgreSQL | `COSMOS_DSN`, `*_ROOT`, `*_STATE` |
| `rdb-loader.env`, `rdb-loader.pgpass` | 그래프 스냅샷 Loader | `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSFILE`, `RDB_LOADER_INPUT_ROOT` |
| `document-loader/common.env`, `document-loader/pgpass` | 뉴스·공시 문서 Loader | `deploy/document-loader/common.env.example` 참고 |
| (공통) | Hadoop 클라이언트 | `HADOOP_HOME`, `HADOOP_CONF_DIR`, `HDFS_BIN`, `JAVA_HOME`, `HADOOP_USER_NAME`, `ARROW_LIBHDFS_DIR` |

OpenDART 키는 DART 수집기 자체 설정 파일로 제공합니다 ([`Crawling/disclosures/dart/README.md`](../Crawling/disclosures/dart/README.md)). 각 모듈의 예시 파일은 `deploy/*/…env.example`, `Crawling/news/deployment/*.env.example`에 있습니다.

## 5. 배포

### 5.1 웹 서비스 (자동)

1. `dev` 브랜치에 push(MR 병합)하면 GitLab Webhook이 Jenkins `cosmos-web` Job을 실행합니다 (`https://j15c205.p.ssafy.io/jenkins/project/cosmos-web`).
2. 파이프라인: 체크아웃 → 프론트 build·ESLint → 백엔드 jar → 임시 PostgreSQL·Redis로 테스트 → 이미지 태그(`<커밋 12자리>-<빌드 번호>`) → `deploy/scripts/deploy.sh` → 헬스체크.
3. 수동 배포·롤백: `sudo -u cosmos-ci bash deploy/scripts/deploy.sh <image-tag>`.
4. 확인: `https://j15c205.p.ssafy.io/healthz`, 백엔드 `/actuator/health` (`127.0.0.1:18081`).

### 5.2 새 서버 최초 구성

```bash
sudo bash deploy/scripts/bootstrap-host.sh      # Docker, UFW(22/80/443), cosmos-ci 계정, Nginx
sudo certbot certonly --webroot -w /var/www/cosmos-acme -d <도메인>
sudo bash deploy/scripts/start-jenkins.sh
sudo bash deploy/scripts/enable-https.sh         # deploy/nginx/cosmos.conf 적용
```

이후 `/etc/cosmos/app.env`를 만들고 Jenkins에 Deploy Token·Webhook을 등록합니다 ([`docs/CICD.md`](../docs/CICD.md)의 "Jenkins에 GitLab 연결").

### 5.3 데이터 클러스터

- Worker 생성·설치: `deploy/hadoop/aws-provision.py`, `install-worker.sh`, `configure-cluster.py`, `configure-firewall.py` ([`deploy/hadoop/README.md`](../deploy/hadoop/README.md)).
- Master systemd 유닛: `cosmos-hadoop-{namenode,secondarynamenode,resourcemanager}`, `kafka`, 수집기 `cosmos-news-collector`, `cosmos-naver-news@{search,deliver}`, `cosmos-news-hdfs-writer`, 주기 작업 timer `cosmos-{dart-daily,sec-incremental,stock-prices-kospi,stock-prices-nasdaq,news-analysis,news-enrich,sentiment-loader,relation-loader,document-loader}`.
- 각 서비스 설치 방법: `deploy/news-analysis`, `deploy/news-enrich`, `deploy/document-loader`, `deploy/stock-prices`, `deploy/rdb-loader`, `Crawling/news/deployment`의 README.

## 6. 배포 시 특이사항

- **포트**: 외부에는 22/80/443만 엽니다. Spring Boot `127.0.0.1:18081`, 프론트 Nginx `127.0.0.1:18080`, Jenkins `127.0.0.1:18090`은 호스트 Nginx 뒤에만 둡니다. Nginx가 `/api` → 18081, `/jenkins/` → 18090, 나머지 → 18080으로 보냅니다.
- **Redis 포트**는 기본 6379가 아니라 `16379`입니다 (운영 Compose).
- **PostgreSQL**은 Docker 내부망에만 있고, Master의 Loader용으로 Tailscale 주소 `100.69.73.112:5432`만 엽니다. UFW도 Master(`100.117.115.44`)에서 오는 5432만 허용합니다.
- **Flyway**가 앱 시작 시 스키마를 바꿉니다 (현재 V9). 이미지 롤백으로 스키마는 되돌아가지 않으므로 파괴적 변경 전에 DB를 백업합니다.
- **릴리스 위치**: `/opt/cosmos/releases/<IMAGE_TAG>/compose.yml`, 현재 `current`, 직전 `.previous`. 헬스체크 실패 시 직전 릴리스로 자동 복구하고, 이미지는 현재·직전 두 개만 남깁니다.
- **Docker 볼륨**: `cosmos_postgres_data`, `cosmos_redis_data`, `cosmos_jenkins_home`. 앱 재배포와 무관하게 유지됩니다.
- **시간대**: DB·백엔드는 UTC, Jenkins는 Asia/Seoul입니다.
- **Hadoop 복제 계수 2**, HDFS 용량 587 GiB 중 278 GiB 사용 (2026-09-28). Worker 데이터 EBS는 `DeleteOnTermination=false`라 인스턴스를 종료해도 남아 비용이 나옵니다.
- **AWS 비용**: Worker 3대 기준 월 약 $332 (크레딧 차감 전). 운영 종료 시 인스턴스와 데이터 EBS를 함께 정리합니다.
- **수집 파이프라인 상태 (2026-09-28)**: 수집기 unit과 주기 timer가 모두 멈춰 있고 disabled 상태입니다. HDFS·YARN·Kafka만 실행 중입니다. 재개하려면 수집기 unit과 timer를 `sudo systemctl enable --now`로 다시 켭니다. `cosmos-news-analysis`, `cosmos-document-loader`는 2026-09-25 마지막 실행이 실패 상태로 남아 있어 재개 전 로그(`journalctl -u <unit>`)를 확인합니다.

## 7. DB 접속 정보와 설정 파일 목록 (ERD 관련)

DB `cosmos` (PostgreSQL 17), 스키마 2개: `public` (웹 서비스, 32 테이블, 약 1.3 GB), `cosmos_analysis` (분석·문서 적재, 17 테이블, 약 3.9 GB). ERD와 테이블 설명은 [`docs/database-design.md`](../docs/database-design.md).

| DB 계정 | 용도 | 권한·접속 정보가 정의된 위치 |
| --- | --- | --- |
| `cosmos` | 테이블 소유자, Spring Boot 접속 | `deploy/compose.yml` (`POSTGRES_USER`, `DB_USERNAME`), 비밀번호 `/etc/cosmos/app.env` `DB_PASSWORD` |
| `cosmos_loader` | 그래프 스냅샷 RDB Loader | Master `/etc/cosmos/rdb-loader.env`, `rdb-loader.pgpass`; 권한 `docs/rdb-loader.md` |
| `cosmos_document_loader` | 뉴스·공시 문서 Loader | Master `/etc/cosmos/document-loader/pgpass`; 권한 `deploy/document-loader/README.md` |
| `cosmos_stock_loader` | 주가 적재 | Master `/etc/cosmos/stock-prices.pgpass` |

속성이 정의된 소스 파일:

| 파일 | 내용 |
| --- | --- |
| `BackEnd/src/main/resources/application.yml` | 공통 설정: DataSource, Redis, Gmail SMTP, Flyway, JPA(`ddl-auto: validate`), JWT 수명, 쿠키, CORS, 이메일 인증 TTL |
| `BackEnd/src/main/resources/application-prod.yml` | 운영 프로필: 포트 18081, 환경 변수 주입, 보안 쿠키, health만 공개 |
| `BackEnd/src/main/resources/db/migration/V1~V9__*.sql` | 스키마 (ERD 원본) |
| `BackEnd/docker-compose.yml` | 로컬 개발용 PostgreSQL·Redis (로컬 전용 비밀번호) |
| `deploy/compose.yml`, `deploy/compose.ci.yml` | 운영·CI 컨테이너 구성 |
| `deploy/.env.example` | 운영 환경 변수 형식 |
| `deploy/nginx/cosmos.conf` | 호스트 Nginx 라우팅·TLS |
| `deploy/jenkins/compose.yml`, `Jenkinsfile` | Jenkins 구성과 파이프라인 |
| `FrontEnd/nginx.conf`, `FrontEnd/vite.config.ts` | 프론트 컨테이너 Nginx, Vite 설정 |

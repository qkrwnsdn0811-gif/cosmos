# 외부 서비스 정보

코드·서버 설정에서 실제로 호출하는 외부 서비스를 정리했습니다 (2026-09-28 기준). 계정 ID·비밀번호·API 키·토큰 값은 이 파일에 적지 않고, 변수 이름과 보관 위치만 적습니다.

- **소셜 로그인은 사용하지 않습니다.** 회원 가입은 이메일 인증 + 닉네임·비밀번호 방식입니다 (백엔드에 OAuth2 의존성 없음).
- **포톤 클라우드·외부 파일 저장소·외부 코드 컴파일 서비스는 사용하지 않습니다.** 이미지는 프론트 번들에 포함합니다.

## 1. 서비스 운영

| 서비스 | 용도 | 가입·발급 방법 | 설정 위치 |
| --- | --- | --- | --- |
| **Gmail SMTP** | 회원 가입 이메일 인증 코드 발송 (`smtp.gmail.com:587`, STARTTLS) | 팀 공용 Google 계정 → 보안 → 2단계 인증 사용 → 앱 비밀번호 생성 | `MAIL_USERNAME`, `MAIL_PASSWORD` (웹 EC2 `/etc/cosmos/app.env`, 로컬 `BackEnd/.env`) |
| **Let's Encrypt (Certbot)** | `j15c205.p.ssafy.io` HTTPS 인증서 발급·자동 갱신 | 가입 없음. `certbot certonly --webroot -w /var/www/cosmos-acme -d <도메인>` | `/etc/letsencrypt`, `certbot.timer`, deploy hook이 Nginx 재적재 |
| **SSAFY GitLab** | 소스 저장소, MR, Jenkins 체크아웃·Webhook | 프로젝트 → Settings → Repository → Deploy tokens (`read_repository`), Webhooks (Push events, `dev`) | Jenkins Credentials `cosmos-git-read`, Webhook 토큰 `/etc/cosmos/jenkins/webhook-token` |
| **Jira (ssafy.atlassian.net)** | 이슈·일정 관리 (`S15P21C205-*`) | SSAFY 제공 계정 | 커밋·MR에 이슈 키 기재 |

## 2. 인프라

| 서비스 | 용도 | 가입·설정 | 비고 |
| --- | --- | --- | --- |
| **AWS EC2 / EBS** | Hadoop Worker 3대 (`m7i-flex.large`, 서울 `ap-northeast-2a`), 데이터 EBS gp3 200 GiB × 3 | AWS 계정 가입 → Free Plan 크레딧, `deploy/hadoop/aws-provision.py`로 생성 | 월 약 $332 (크레딧 차감 전). 데이터 EBS는 인스턴스 종료 후에도 남으므로 별도 삭제. 상세 `docs/HADOOP_CLUSTER.md` |
| **Tailscale** | SSAFY EC2 2대와 AWS Worker 3대를 잇는 사설망 (Hadoop·Kafka·PostgreSQL 트래픽) | tailscale.com 가입 → Tailnet 생성 → 각 서버 `tailscale up` 후 관리 콘솔에서 장치 승인 | 버전 1.102.4. 주소: 웹 `100.69.73.112`, Master `100.117.115.44`, Worker `100.104.188.115`·`100.68.206.1`·`100.124.47.107` |
| **Docker Hub** | 베이스 이미지 (`eclipse-temurin:21`, `node:24-alpine`, `nginx:stable-alpine`, `postgres:17-alpine`, `redis:7-alpine`, `jenkins/jenkins:2.568.3-jdk21`) | 가입 불필요 (익명 pull) | 익명 pull 횟수 제한 있음 |

## 3. 데이터 수집 원천

| 서비스 | 수집 내용 | 키·가입 | 설정 위치 |
| --- | --- | --- | --- |
| **네이버 검색 API** (`openapi.naver.com`) | 코스피 100종목 뉴스 검색 | [NAVER Developers](https://developers.naver.com) → 애플리케이션 등록 → 검색 API 선택 → Client ID/Secret 발급. 일 25,000회 한도 | Master `/etc/cosmos/naver-news.env` (`NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `NAVER_DAILY_BUDGET`) |
| **OpenDART** (`opendart.fss.or.kr`) | 국내 전자공시 목록·원문 | [OpenDART](https://opendart.fss.or.kr) 회원 가입 → 인증키 신청 (일 20,000회) | DART 수집기 설정 파일, `Crawling/disclosures/dart/README.md` |
| **SEC EDGAR** (`data.sec.gov`, `www.sec.gov`) | Nasdaq-100 기업 미국 공시 | 가입·키 없음. 연락처 이메일이 들어간 User-Agent 필수, 초당 10회 이하 | Master `/etc/cosmos/sec-user-agent`, `sec-incremental.env` (`SEC_REQUEST_DELAY`) |
| **Yahoo Finance** (`finance.yahoo.com`) | 해외·코스피 종목 뉴스 목록 | 가입 없음 (공개 페이지) | `Crawling/news` 수집기 |
| **Investing.com** (`www.investing.com`, `kr.investing.com`) | 해외·국내 종목 뉴스 | 가입 없음 (공개 페이지) | `Crawling/news` 수집기 |
| **국내 언론사 사이트** | 기사 본문 (robots.txt 준수) | 가입 없음 | `Crawling/news` (Readability 추출) |
| **KRX / 네이버 금융 / FinanceDataReader** | 코스피·나스닥 100종목 일봉 | 가입 없음 (`pykrx`, `finance-datareader` 라이브러리) | Master `/etc/cosmos/stock-prices.env` |
| **Nasdaq 지수 페이지** (`indexes.nasdaq.com`) | Nasdaq-100 구성 종목 | 가입 없음 | SEC 수집기 대상 목록 |
| **GDELT** | 코스피 뉴스 보조 수집 | 가입 없음 | 저장소에만 있고 서버에는 설치하지 않았습니다 (`Crawling/news/docs/GDELT_KOSPI.md`) |

## 4. AI 모델

| 서비스 | 용도 | 비고 |
| --- | --- | --- |
| **Hugging Face Hub** | 감성 분석 모델 `ProsusAI/finbert` 다운로드 | 가입·토큰 불필요 (공개 모델). `AI/gpu_news/download_models.py`로 한 번 받아 로컬 경로에 둡니다. 서버는 CPU로 추론하고 대량 재처리만 GPU 워크스테이션(WSL2, CUDA 12.4)에서 실행 (`deploy/gpu-news-worker/README.md`) |
| **PyTorch 휠 저장소** (`download.pytorch.org`) | `torch==2.6.0` CUDA 12.4 빌드 설치 | 가입 불필요 |

## 5. 개발 시에만 쓰는 외부 자원

| 서비스 | 용도 |
| --- | --- |
| logo.dev, Clearbit, DuckDuckGo·Google favicon | `FrontEnd/scripts/fetch-logos.mjs`로 기업 로고를 받아 `FrontEnd/public`에 저장. 운영 화면은 외부에 요청하지 않습니다. |
| npm registry, PyPI, Maven Central, Gradle 배포 서버 | 빌드 의존성 설치 |

## 인계 시 확인할 것

1. Gmail 앱 비밀번호, 네이버 Client Secret, OpenDART 키는 저장소 밖(접근 제한된 채널)으로 전달하고, 인계 후 재발급을 권장합니다.
2. AWS 계정의 크레딧·Free Plan 만료일(2027-03-11 예정)을 확인하고, 운영을 끝낼 때 Worker 인스턴스와 데이터 EBS 3개를 함께 삭제합니다.
3. Tailscale 관리 계정을 넘기거나, 새 Tailnet으로 옮길 때 모든 서버를 다시 가입시키고 Hadoop·UFW의 IP 설정(`deploy/hadoop/configure-firewall.py`, inventory)을 새 주소로 바꿉니다.
4. GitLab Deploy Token 만료일을 확인합니다.

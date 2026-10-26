# NEXUS 실시간 수집 (GCP 시연 서버)

빅카인즈 뉴스를 서버가 스스로 받아 분석·적재한다. 앱 배포(GitHub Actions → `deploy/compose.yml`)와는 따로 돈다 — CI/CD 는 DB 를
건드리지 않고, 이 파이프라인만 DB 에 뉴스·이벤트·과거 사례를 쓴다.

| 주기 | 무엇 | 걸리는 시간 |
|---|---|---|
| 15분마다 (`nexus-realtime.timer`) | `/changeTracker/news` 로 그사이 바뀐 기사 → `news_ids` 상세 → `경제>반도체` 만 → 지난 `analyze` 의 관계 그래프로 새 기사만 분석 → 이벤트 적재·취소 기사 내리기 → 새 이벤트가 있으면 `cases`(뉴스 유형·방향 표시·피드 순서) | 새 기사 없으면 수십 초, 있으면 `cases` 포함 수 분 |
| 18:40 이후 첫 실시간 실행 | 위 + 확정 일봉으로 주가·과거 사례 다시 계산, 뉴스 근거 문장 | + 수 분 |
| 매일 03:30 (`nexus-nightly.timer`) | 지난달 1일부터 다시 수집 → `analyze`(관계 그래프부터) → `load` → `cases` → `evidence` → (DART 키가 있으면) `fundamentals`·`ownership` → `publish-graph` | 1~2시간 |

둘은 같은 파일(`events.pkl`)을 쓰므로 잠금(`/run/lock/nexus-pipeline.lock`)으로 하나씩만 돈다. 밤 갱신 중이면 실시간 주기는 건너뛴다.

## 빠진 것 (개발 PC 판과 다른 점)

- **기사 감성(KR-FinBERT)**: 서버에는 GPU·torch 가 없어 서버에서는 돌리지 않는다. GPU PC 가 30분마다 채운다(아래 "기사 감성은 GPU PC 에서").
- **KPF-BERT 관계 판별기**: 학습한 모델 파일이 서버에 없으면 규칙만으로 근거 관계를 찾는다(`load_predictor` 가 None).
  개발 PC 의 `nexus/data/relations/model/` 을 `nexus_data` 볼륨의 같은 자리에 넣으면 다음 밤 갱신부터 쓴다.
- 장중 시세는 없다. 주가는 하루 한 번 확정 일봉이다.

## 처음 구성 (2026-10-26)

```bash
# 1. 코드 — 앱 배포 체크아웃(/opt/cosmos/checkouts/cosmos, CI 가 detach 로 옮긴다)과 따로 둔다
sudo git clone https://github.com/qkrwnsdn0811-gif/cosmos.git /opt/nexus-pipeline
# 2. 키 — 저장소·채팅에 남기지 않는다. (있으면) DART_API_KEY=... 도 같은 파일에
sudo install -m 0640 /dev/stdin /etc/cosmos/nexus.env <<< 'BIGKINDS_ACCESS_KEY=...'
# 3. 이미지
cd /opt/nexus-pipeline && sudo docker compose -f deploy/nexus-realtime/compose.yml build
# 4. 타이머 — 이것만 켜면 된다
sudo cp deploy/nexus-realtime/nexus-{realtime,nightly}.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now nexus-realtime.timer nexus-nightly.timer
```

첫 15분 주기가 첫 전체 실행(bootstrap)을 한다. 운영 DB 는 그대로 두고 새 DB `cosmos_next` 를 Flyway 로 만든 뒤, 기업 마스터 →
2025-01 부터 수집 → 분석 → 적재 → 과거 사례 → 은하를 채운다(1~3시간). 활성 이벤트가 5만 건 이상이면 백엔드를 잠깐 멈추고
`cosmos` ↔ `cosmos_next` 이름을 바꾼다. 옛 DB 는 `cosmos_old` 로 남는다. 실패하면 1시간 뒤 다시 시도하고, 받은 달은 캐시에서 이어 받는다.
끝나면 볼륨에 `realtime/bootstrapped` 가 생기고, 그다음 주기부터 실시간 수집만 돈다. 밤 갱신은 그 전까지 건너뛴다.

되돌리기(옛 DB 로):

```bash
B=$(sudo docker ps -q --filter label=com.docker.compose.project=cosmos --filter label=com.docker.compose.service=backend)
sudo docker stop "$B"
sudo docker exec cosmos-postgres-1 psql -U cosmos -d postgres   -c 'ALTER DATABASE cosmos RENAME TO cosmos_failed' -c 'ALTER DATABASE cosmos_old RENAME TO cosmos'
sudo docker start "$B"
```

## 기사 감성은 GPU PC 에서

서버에는 GPU 가 없다. GPU 가 있는 Windows PC 에서 `gpu-sentiment.ps1` 이 30분마다 서버의 점수 없는 기사를 받아(`run.sh sentiment-export`)
KR-FinBERT 로 매기고 되돌려 보낸다(`run.sh sentiment-import` — 감성 캐시에 합치고 뉴스 화면 논조를 DB 에 쓴다). PC 가 꺼져 있으면 새 기사는
점수 없이(중립으로) 보이다가 다음 실행 때 채워진다.

```powershell
# 한 번: CUDA PyTorch 와 모델 의존성 (RTX 4070·드라이버 591 에서 cu130 휠 확인)
python -m venv nexus\.venv-cuda
nexus\.venv-cuda\Scripts\python -m pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu130
nexus\.venv-cuda\Scripts\python -m pip install -e "nexus[ml]"
# 작업 스케줄러 등록 (지우기는 파일 머리 참고)
powershell -ExecutionPolicy Bypass -File deploy
exus-realtimeegister-gpu-sentiment.ps1
```

## 확인

```bash
systemctl list-timers 'nexus-*'
sudo journalctl -u nexus-realtime --since today | tail -20      # "changes since …: updated N, cancelled M, 반도체 K"
sudo docker exec cosmos-postgres-1 psql -U cosmos -d cosmos -c \
  "SELECT count(*), max(dateline) FROM news_event WHERE value_chain = 'SEMICONDUCTOR' AND status = 'ACTIVE'"
```

체크포인트는 볼륨 안 `realtime/semis.json` 이다(마지막 성공 시각·마지막 주가 갱신일). 중간에 실패하면 옮기지 않으므로 다음 실행이 같은
구간을 다시 본다. 3일 넘게 멈췄으면 그 앞은 밤 갱신의 `collect` 가 메운다.

## 수동 실행

```bash
sudo /opt/nexus-pipeline/deploy/nexus-realtime/run.sh realtime    # 지금 한 번
sudo systemctl start nexus-nightly                                  # 밤 갱신을 지금
```

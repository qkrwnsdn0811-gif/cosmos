# 스토리 3 AI 모델 — 백엔드 전달 요약

뉴스 원문을 입력하면 **직접 언급된 기업과 관계로 연결된 기업을 찾고, 기업별 영향 방향과 상대 강도를 반환**하는 모델입니다. 모델 학습과 Python 내부 API 구현은 완료했으며, Spring·프론트 연결과 운영 배포는 아직 진행하지 않았습니다.

## 1. 어떤 방식으로 결과를 만드는가

```text
뉴스 제목·본문·발행일·국내외 구분
    ↓
불필요한 주가 위젯 등 제거 + 기사에 언급된 기업 식별
    ↓
뉴스의 의미와 금융 감성을 숫자 특징으로 변환
    ↓
기사 시점에 사용 가능한 기업 관계 그래프에서 최대 2홉 후보 탐색
    ↓
2층 GNN(GATv2)으로 기업별 방향·강도 계산
    ↓
학습된 GNN 3개의 결과를 평균하고 강도순으로 반환
```

뉴스 특징은 직접 언급된 기업에 주입하고, GNN이 기업 간 관계를 따라 정보를 전달하도록 학습했습니다. 관계에는 공급·협력·경쟁·지분·공동 언급·과거 가격 상관 등이 포함됩니다.

학습 정답은 수정주가에서 시장 지수 움직임을 제거한 **1·3거래일 반응**으로 만들었습니다. 기사 가용 시점 이후 첫 종가를 기준으로 그다음 1·3거래일을 측정하므로, 기사 직후의 장중 반응을 뜻하지 않습니다. **예측 요청에는 실시간 주가를 보낼 필요가 없습니다.**

## 2. 백엔드가 보내는 입력

`POST /v1/impact/predict`

아래는 요청 형식을 설명하기 위한 가상 기사입니다.

```json
{
  "news_id": "example-news-001",
  "title": "삼성전자, 신규 공급 계약 체결",
  "body": "삼성전자가 신규 공급 계약을 체결했다고 밝혔다.",
  "published_at": "2026-09-14",
  "region": "domestic",
  "horizon": 3,
  "model_ver": "stage3",
  "top_k": 20
}
```

- `news_id`: 서비스의 뉴스 ID를 그대로 사용합니다.
- `title`, `body`: 원문 제목과 본문입니다. 요약을 원문 본문으로 대체하지 않습니다.
- `published_at`: 날짜 또는 시간대가 포함된 발행 시각입니다. 날짜만 알면 날짜만 전달합니다.
- `region`: 수집 원본 기준 `domestic` / `overseas`입니다.
- `horizon`: 1 또는 3거래일입니다. `model_ver: "stage3"`가 GNN이며, `top_k`는 최대 32입니다.

## 3. 백엔드가 받는 결과

응답의 `companies` 배열에 영향 강도순으로 기업별 결과가 담깁니다.

| 필드 | 의미 및 처리 |
|---|---|
| `ticker`, `name` | 종목 코드와 기업명. `ticker`로 서비스 기업 ID와 연결 |
| `impact_dir` | 모델이 추정한 시장 대비 반응 방향: `-1` 부정 / `0` 중립 / `1` 긍정 |
| `impact_score` | `0~1` 상대 영향 강도. 화면에서 100을 곱해 점수로 표시 가능 |
| `direction_probabilities` | 부정·중립·긍정 클래스별 모델 확률 출력. 별도 확률 보정은 미적용 |
| `is_direct` | 기사에 직접 언급된 기업인지 여부 |
| `path` | 직접 언급 기업에서 해당 후보로 이어지는 관계 경로. 직접 언급 기업은 빈 배열 |
| `explanation` | 일반 예측에서는 `null`. 별도 설명 요청으로 생성 |
| `limited_training_history` | 해당 기업의 학습 표본이 적다는 표시 |

응답 상위에는 `news_id`, `status`, `model_ver`, `model_version`, `horizon_sessions`, `graph_as_of` 등도 포함됩니다. 기업을 찾지 못하면 `status: "NO_LINKED_COMPANIES"`와 빈 `companies` 배열을 반환합니다. 잘못된 요청은 HTTP 422로 처리합니다.

**`impact_score = 0.7`은 상승 확률 70%나 예상 수익률 70%를 뜻하지 않습니다.** 방향과 강도는 따로 해석하며, 기존 관계 점수·관련도·감쇠율을 결과에 다시 곱하지 않습니다.

## 4. 근거 설명은 별도 요청

기업을 클릭했을 때 `POST /v1/impact/explain`에 예측과 같은 입력 및 `target_ticker`를 전달하면 됩니다. 대상은 해당 기사의 후보 기업이어야 합니다.

GraphRAG가 관계 근거와 과거 기사를 검색하고, 별도 언어 모델이 설명을 생성합니다. 원문 인용·숫자·날짜·문장의 함의를 검사하며, 결과 상태에 따라 생성 설명 또는 추출형 근거를 반환하거나 설명 생성을 거절합니다. **설명 모델은 GNN의 방향·강도 점수를 바꾸지 않습니다.** 숫자 결과를 먼저 표시하고 설명은 별도 로딩으로 처리하면 됩니다.

## 5. 연결할 때 필요한 작업과 현재 상태

- 연결 구조는 **프론트 → Spring → Python AI API**입니다. 백엔드는 뉴스 원문을 확보해 요청하고 결과를 서비스 응답에 연결합니다.
- 현재 확인한 뉴스 저장 구조에는 HDFS 경로가 있으므로, 원문 제목·본문과 국내외 구분을 읽어 전달하는 부분이 필요합니다.
- AI 실행 환경에는 GNN 가중치뿐 아니라 뉴스 인코더·그래프·기업 사전·전처리 설정이 필요합니다. 이를 묶은 모델 번들을 별도로 전달합니다. 프론트에는 모델 파일이 필요하지 않습니다.
- 현재 모델 버전은 `66c54ef0207b4783`입니다. 내부 API는 구현되어 있지만 운영 호출 주소는 아직 정해진 상태가 아닙니다.
- 학습·기능 검증은 완료했지만, **단순 기준선보다 전반적으로 잘 예측한다는 점은 입증하지 못했습니다.** 출력은 모델의 추정치이며, 관계 경로 역시 해당 뉴스가 주가 변화를 일으켰다는 인과 증거는 아닙니다.

## 6. 기존 모델을 HDFS로 전달하고 AI 서버에서 실행하기

**HDFS에는 번들을 보관하고, AI 서버는 이를 로컬 디스크로 내려받아 Python HTTP API로 실행합니다.** Spring은 실행 중인 API를 호출합니다. HDFS에 파일을 올리는 것만으로 예측 서버가 실행되지는 않습니다.

전달 대상은 `AI/graph/artifacts/news_impact_v2_bundle/` 전체입니다. `metadata.json`의 버전은 **`66c54ef0207b4783`**, 기업 수는 **202개**이며, `bundle_manifest.json`에 기록된 46개 파일의 합계는 **5,139,749,357바이트(압축 전 약 5.14GB)**입니다. GNN 가중치뿐 아니라 `models/` 아래 인코더·감성·설명 모델, 그래프·사전·설정·검색 기사도 포함하므로 폴더 구조를 유지합니다. 수신 후 manifest의 파일별 크기와 SHA-256을 대조합니다.

번들과 함께 **검증한 Python 소스와 requirements도 전달**해야 합니다. 저장소의 `AI/graph/`와 `AI/ner/` 구조를 유지합니다(`AI/ner/matcher.py`도 사용). `requirements-news-impact.txt`는 `requirements-impact.txt`를 포함하므로 두 파일이 모두 필요합니다. requirements에 기록된 검증 환경은 **Python 3.12**이며 CPU 추론을 지원합니다. 현재 진행 중인 데이터 규모 실험과 이 기존 전달 번들은 분리해 관리합니다.

이번 전달의 HDFS 버전 경로는 `/data-lake/sandbox/news/junwoo/models/news_impact_v2/66c54ef0207b4783`입니다. 그 아래 `news_impact_v2_bundle/`에 모델 전체, `BACKEND_MODEL_HANDOFF.md`에 이 문서, `upload_receipt.json`에 전송 검증 기록을 둡니다. 완료 여부는 검증 기록을 확인합니다.

아래는 Hadoop 클라이언트가 설정된 Linux AI 서버의 **실행 방법 예시**입니다. `/opt/cosmos/...`는 예시 로컬 설치 경로이므로 실제 배포 경로로 바꿉니다. 저장소 소스가 `/opt/cosmos/S15P21C205`에 준비됐다고 가정합니다.

```bash
cd /opt/cosmos/S15P21C205
python3.12 -m venv .venv
.venv/bin/python -m pip install -r AI/graph/requirements-news-impact.txt

mkdir -p /opt/cosmos/model_store
hdfs dfs -get /data-lake/sandbox/news/junwoo/models/news_impact_v2/66c54ef0207b4783/news_impact_v2_bundle /opt/cosmos/model_store/

# 내려받은 번들의 manifest 대조 후 실행
.venv/bin/python AI/graph/serve_news_impact.py \
  --artifacts /opt/cosmos/model_store/news_impact_v2_bundle \
  --host 0.0.0.0 --port 8092
```

같은 서버의 다른 터미널에서 로딩 상태를 확인합니다.

```bash
curl --fail http://127.0.0.1:8092/health
```

정상 기동 시 코드가 반환하는 예상 형식은 다음과 같습니다. **실제 서버에서 확인한 응답은 아닙니다.**

```json
{"status":"ok","model_version":"66c54ef0207b4783","primary_model":"stage3","company_count":202}
```

`0.0.0.0`은 수신 주소입니다. Spring의 호출 주소에는 AI 서버의 실제 IP/호스트명을 사용합니다(예: `http://ai-server:8092/v1/impact/predict`). `/health`는 모델 로딩 상태를 확인하며 예측 품질이나 Spring 연동 완료를 뜻하지 않습니다. **모델 파일 전달과 운영 설치·서버 기동·서비스 연결은 별도 작업이며, 운영 설치와 연결은 아직 수행하지 않았습니다.**

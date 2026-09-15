# cosmos 뉴스 영향도 모델

**이 문서는 v1 실측 기록이다.** 지수 라벨·과거 지분 이력·검증된 설명문을 보강한
v2 안내는 [README_NEWS_IMPACT_V2.md](README_NEWS_IMPACT_V2.md)를 먼저 읽는다.
사용자는 서비스 연결 없이 모델 학습·검증까지만 진행하도록 결정했다.

원래 [스토리 3 명세](https://app.notion.com/p/3cf75ef134b8805a8f9af72eb521df97)는
기사 내용과 기업 그래프로 기업별 **방향(+1/0/-1)·강도(0~1)**를 예측하고, 요청 시 근거를
설명하는 것이다. 이 파이프라인은 그 명세를 구현한다. 소스 기업만 입력하는 이전 U3는
`README_IMPACT_GNN.md`의 별도 실험으로 보존한다.

## 구성

| 단계 | 구현 | 입력과 출력 |
|---|---|---|
| Stage1 | 금융 감성 규칙 | KR-FinBERT/FinBERT 감성 × 언급 위치·횟수 × 2홉 경로 가중치·부호 |
| Stage2 | LightGBM | 뉴스 임베딩·감성·기업 ID·언급 정보·관측 가능한 속성 → 방향·강도 |
| Stage3 (주모델) | PyG 2층 GATv2, 3시드 앙상블 | 직접 언급 노드에 뉴스 입력, 관계 유형별 attention → 1/3일 방향·강도 다중 과제 |
| 설명 | GraphRAG | 그래프 경로·시점 이전 근거 검색 → 로컬 Qwen이 근거 문장 선택·인용 |

Stage2에는 경로 길이·관계 가중치·이웃 특징을 넣지 않는다. 후보 기업 집합은 모든 모델에
동일하게 적용한다. Stage2/3는 같은 다국어 MiniLM 임베딩을 train PCA64로 투영해 사용한다.
Stage3 attention은 학습된 메시지 전달 가중치이며, 인과 효과의 증명이 아니다.

기사 제목·본문 앞부분을 최대 256 wordpiece로 임베딩하고, 금융 감성은 제목+본문 첫 200자를
128 wordpiece로 분석한다. 장문 전체나 한 기사 속 기업별 상반된 입장을 완벽하게 이해하는
모델로 소개하지 않는다. 한국어는 KR-FinBERT-SC, 영어는 ProsusAI FinBERT를 사용한다.

## 실측 결과 (정제 후 최종 실행, 2026-09-15)

모델 버전 `abc4be78e5933b99`. LightGBM+GAT 학습·평가 351.3초.
GAT는 시드 0/1/2의 검증 손실로 각각 epoch 5/2/1을 선택하고 확률·강도를 평균했다
(epoch는 0부터 센다). test 결과를 보고 모델을 고르거나 다시 조정하지 않았다.

동일 test 기사×기업 표본의 **방향 정확도**:

| 시장·기간 | 다수 클래스 | 감성 규칙 | LightGBM | GAT |
|---|---:|---:|---:|---:|
| KOSPI 1일 | 43.07% | 28.81% | 41.92% | 41.91% |
| KOSPI 3일 | 43.35% | 28.99% | 41.89% | 42.43% |
| NASDAQ 1일 | 36.84% | 31.44% | 35.27% | 37.07% |
| NASDAQ 3일 | 35.74% | 31.32% | 32.19% | 35.81% |

KOSPI 152,253쌍, NASDAQ 122,685쌍으로 모든 모델이 같다. GAT는 NASDAQ에서 LightGBM보다
높지만, **다수 클래스 대비 우위를 입증하지 못했다**. KOSPI는 다수 클래스보다 낮다.
"GNN을 학습·서비스할 수 있다"와 "주가 방향을 잘 맞힌다"는 구분해야 한다.

| GAT 지표 | KOSPI 1일 | KOSPI 3일 | NASDAQ 1일 | NASDAQ 3일 |
|---|---:|---:|---:|---:|
| 기사별 기업 순위 IC 평균 (부호 포함) | 0.0289 | 0.0693 | 0.0153 | -0.0213 |
| 전체 표본 강도 IC (절댓값) | 0.1202 | 0.1478 | 0.1868 | 0.1734 |

기사별 IC는 같은 기사 내 변별력이고, 전체 표본 강도 IC는 기업별 변동성 차이도 포함한다.
이를 같은 지표로 섞지 않는다. 이전 U3 IC와도 라벨·입력·표본이 달라 직접 비교하지 않는다.
CSV의 `article_rank_ic`는 예측이 상수일 때 정의되지 않아 유효 기사 수를 함께 기록한다.
모델 간 비교에는 `common_article_rank_score`를 사용한다. 이는 모든 모델에 같은 기사 집합을
적용하고 상수 예측은 순위 정보 0으로 채점한 지표다. `n_rank_articles`가 모델마다 같음을 감사한다.

세션별 정확도 차이를 평균하고 HAC 5lag로 보정한 다수 클래스 대비 차이:
KOSPI 1/3일 -1.01/-1.15%p(t=-5.59/-4.06), NASDAQ +0.47/+0.67%p(t=0.53/0.66).
이는 위 표의 **쌍별 평균 정확도 차이**와 가중 방식이 다르다. 단순히 표를 빼서 이 t값을 붙이지 않는다.

실험 결과상 방향 정확도 개선은 다음 모델 연구 과제로 남는다. 현재 산출물은 GNN을 주모델로
실행하는 내부 AI 서비스이며, 정확도가 검증된 투자 예측 서비스로 소개하지 않는다.

## 데이터와 라벨

- 202종목. HDFS `20260908-prepared` 원본 기사 + dict-v1.3 고신뢰 멘션을 사용.
- 연도·국내외별 최대 1,500건을 뉴스 ID의 SHA256 순서로 추출. 원본 표본 32,621건.
- 정제 후 학습/평가 대상 기사 **29,835건**, 기사×기업 **844,353쌍**.
- train 2017~2022 / val 2023 / test 2024~2026-09-11. 2016은 그래프 준비에 사용.
- 본문 대표 기사 필터, 정제 텍스트 중복 제거, 20기업 초과 나열 기사 제외. 주가 결과로 표집하지 않음.
- 발행 시각이 없어 국내는 서울 해당일 종료, 해외는 시간대 미상으로 UTC-12 해당일 종료를
  정보 이용 가능 시점으로 잡는다. 그 이후 **첫 종가를 기준**으로 +1/+3거래일 수익률을 측정한다.
- `adj_close` 로그 수익률에서 같은 시장의 다른 종목 동일가중 수익률을 뺀다. 공식 KOSPI/
  NASDAQ 지수 초과수익률이 아니라 **유니버스 기반 시장 대리 지수 조정값**이다.
- `exchange_calendars`로 거래일·DST·조기 마감·한국 과거 거래시간 변경을 처리한다.
- 종료일이 다음 분할에 걸치는 라벨, 결측 수정주가가 끼는 라벨은 제외. 가격을 forward fill하지 않음.
- 중립 경계는 train |수익률|의 33백분위, 강도 척도는 95백분위. 강도 정답은
  `min(|초과 로그수익률| / train_95백분위, 1)`. 확률·퍼센트 수익률로 표시하지 않는다.

이는 **지연된 일별 반응의 대리 라벨**이다. 기사 공개 직후 분 단위 반응이나 해당 뉴스 하나의
순수한 인과 효과는 측정하지 못한다. 동일 기업의 여러 뉴스와 시장 사건이 같은 수익률에
영향을 주며, 3일 창은 겹친다. 비교 결과에는 동일 세션 묶음과 HAC(5lag) 불확실성을 함께 기록한다.

### 이번에 추가로 차단한 누설

과거 기사 본문에서 수집 시점의 주가 위젯(예: `삼성전자(251,250원 ▼9,750 -3.74%)`)을 발견했다.
첫 학습은 중단·격리했고 그 숫자는 결과로 사용하지 않았다. `news_content.py`가 주가 위젯,
삽입 헤드라인, 사이드바를 제거한다. 원본은 보존한다. 전처리가 적용된 기사는 3,593건이다.
신문사가 역사 기사를 사후 수정하지 않았다는 것까지 보증할 수는 없다.

### 그래프의 시간 기준

연초 이전 관계 근거 문장, 이전 표본 공동언급, 직전 연도 수정주가 상관으로 연도별 그래프를
만든다. 공동언급은 표본의 `count/(count+5)`이며 전체 모집단 PMI라고 부르지 않는다.
현재 `edges_correlation.csv`나 전체 기간 관계 점수는 사용하지 않는다. 날짜만 있는 해외 근거의
가용 시간을 고려해 연초 그래프는 해당일 **12:00 UTC부터 사용 가능**한 것으로 취급한다.

현재 업종/지분 정보는 관측·공시 날짜 이전에 쓰지 않고, 과거 산업 속성은 미상 마스크를 쓴다.
따라서 역사 학습에서는 ownership/sector가 대부분 빠진다. 네 유형 지원 코드가 있다는 사실을
네 유형 전부의 역사적 효과를 검증했다는 뜻으로 소개하지 않는다. 더 최신 구조를 담은 `serving`
스냅샷은 저장하지만, API는 평가한 연도별 그래프만 사용한다. 새로운 관계 유형·업종 정보가
대거 추가된 스냅샷으로 바꾸려면 별도 학습·평가가 필요하다.

## 실행

저장소 루트의 PowerShell에서 실행한다. Python은 프로젝트 `AI/ner/.venv`만 사용한다.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
& C:/Users/SSAFY/.local/bin/uv.exe pip install --python AI/ner/.venv/Scripts/python.exe -r AI/graph/requirements-news-impact.txt
AI/ner/.venv/Scripts/python.exe AI/graph/prepare_news_models.py
AI/ner/.venv/Scripts/python.exe AI/graph/news_impact_data.py --news AI/graph/artifacts/news_impact/raw_news.jsonl
AI/ner/.venv/Scripts/python.exe AI/graph/news_text_features.py
AI/ner/.venv/Scripts/python.exe AI/graph/train_news_impact.py --seeds 3 --epochs 30 --batch-size 24
AI/ner/.venv/Scripts/python.exe AI/graph/audit_news_impact.py
AI/ner/.venv/Scripts/python.exe AI/graph/serve_news_impact.py --port 8092
```

원본 표본 추출은 `spark/export_impact_news.py`를 기존 개인 서버 작업 폴더에서 Spark로 실행한다.
HDFS는 읽기만 하며 `--out`은 새 로컬 파일이어야 한다. 이력과 정확한 원본 경로는
`raw_news.manifest.json`에 저장한다. 재학습할 때는 기존 산출물을 보관한 뒤 실행한다.

## 내부 API

`POST /v1/impact/predict`:

```json
{
  "news_id": "example-001",
  "title": "삼성전자, 신규 공급 계약 체결",
  "body": "삼성전자가 신규 공급 계약을 체결했다고 밝혔다.",
  "published_at": "2026-09-14",
  "region": "domestic",
  "horizon": 1,
  "model_ver": "stage3",
  "top_k": 20
}
```

위 문장은 입력 형식 예시이며 실측 사례가 아니다. `published_at`은 날짜 또는 명시적 시간대가
있는 ISO timestamp다. `horizon`은 1/3, `model_ver`는 stage1/stage2/stage3이다. 가격·임의 점수·
임의 멘션을 요청에 추가하면 거절한다. 추출 기업이 없으면 `NO_LINKED_COMPANIES`를 반환한다.

기업별 반환:

| 필드 | 의미 |
|---|---|
| news_id, ticker | 뉴스와 기업 식별자 |
| impact_dir | -1 부정 / 0 중립 / +1 긍정 |
| impact_score | 0~1 영향 크기. 방향 확률이나 수익률이 아님 |
| direction_probabilities | 세 클래스의 모델 출력. 별도 확률 보정은 미실행 |
| is_direct | 직접 언급 여부 |
| path | 최대 2홉의 실제 src/dst/type/weight/reverse |
| explanation | 기본 null. 별도 요청으로 생성 |
| limited_training_history | 해당 기업의 train 라벨 100쌍 미만 여부 |
| model_ver | 실제 사용 단계 |

응답 상위에는 모델 번들 버전, 그래프 기준일/가용 시각, 라벨 규칙, 기간을 넣는다.

`POST /v1/impact/explain`은 같은 입력에 `target_ticker`를 추가한다. 모델이 같은 점수를 계산한
뒤 GraphRAG를 실행한다. 대상이 후보에 없으면 거절한다. `grounding`에는 원본 record_id,
날짜, 근거 종류, 문장, 사용 LLM revision을 반환한다. 원본 URL이 없으므로 URL을 지어내지 않는다.

## GraphRAG의 범위

실제 Qwen2.5-1.5B-Instruct를 로컬에서 실행한다. 자유로운 설명 초안이 서로 다른 연도의
기사를 혼합한 사례가 있어, 현재는 **LLM이 관련 근거 문장을 선택·인용하는 추출형 설명**이다.
생성 문장이 지정한 원문에 정확히 존재하는지 검사한다. 없는 출처·변형된 문장·점수 필드가
나오면 `GENERATION_REJECTED`, 설명 null과 검색 근거를 반환한다. 템플릿 출력을 LLM 생성으로
위장하지 않는다. 문서 내용은 명령으로 실행되지 않으며 LLM에 도구 권한이 없다.

관계 근거가 있다는 것만으로 현재 뉴스가 그 기업의 주가를 움직였다는 뜻은 아니다.
co_mention/correlation 경로는 공동 등장/가격 동조이며 계약이나 공급 사실의 근거로 사용하지 않는다.

## 산출물과 검증

회귀 테스트 20개, 실제 국내·해외 기사 × 3단계 × 2기간의 CPU 재로딩 대조 12건과 실제
localhost HTTP 검증을 통과했다. 점수·확률 최대 오차는 **8.64e-7**이었다.
기사 내용을 바꾸면 출력이 달라지고, 실시간 주가 위젯 값만 바꾸면 결과가 동일함을 확인했다.
이는 입력 민감도 검증이며 뉴스 내용의 추가 예측력을 입증한 실험은 아니다.
CPU HTTP 일반 예측은 워밍업 후 중앙값 **141ms**, 설명 생성은 **40.8초**(단일 사례)였다.
설명을 요청해도 GNN 방향·강도는 변하지 않는다. 설명은 점수 계산 lock 밖에서 실행한다.
테스트용 서버는 검증 후 종료했다. 운영 서버 배포나 웹 연결은 하지 않았다.

`artifacts/news_impact/`는 git에서 제외한다. 코드만 clone해서 모델이 자동으로 존재하지 않는다.
주요 산출물은 `gat_seed*.pt`, `lightgbm_*.txt`, `pretrained.json`과 `hf/`, `graphs.json`,
`companies.json`, `text_projection.npz`, `label_config.npz`, `metadata.json`이다. 설명 검색에는
`articles.jsonl`이 추가로 필요하다. 배포 패키지에는 이 파일들과 NER 사전 자료를 함께 전달한다.
학습용 `raw_news.jsonl`, `pairs.parquet`, `embeddings.npy`는 점수 추론에 필요하지 않다.

평가: `evaluation.csv`, `paired_session_comparison.csv`, `paired_hac_comparison.csv`.
추적: `dataset_audit.json`, `training_history.csv`, `audit_manifest.json`.
중단한 정제 전 실험은 `attempt_unsanitized_aborted/`에 따로 보관했다.

```powershell
AI/ner/.venv/Scripts/python.exe -m unittest discover -s AI/graph/tests -v
```

역사적 NLP 모델·사전 버전, 현재 생존 종목 202개, 비슷한 내용의 다른 기사, 발행 시각 미상은
남아 있는 제약이다. 완벽한 당시 실시간 백테스트나 독립 뉴스 인과 효과로 발표하지 않는다.
Spring/프론트 연결과 운영 서버 배포는 이 AI 모델·API 구현 범위와 별도다.

## 사용 모델·라이브러리 근거

- [다국어 MiniLM](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
- [KR-FinBERT-SC](https://huggingface.co/snunlp/KR-FinBert-SC), [FinBERT](https://huggingface.co/ProsusAI/finbert)
- [PyG GATv2Conv](https://pytorch-geometric.readthedocs.io/en/latest/generated/torch_geometric.nn.conv.GATv2Conv.html)
- [Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct)
- [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars)

실제 사용 revision은 `pretrained.json`, 패키지 버전은 requirements와 학습 메타데이터를 따른다.

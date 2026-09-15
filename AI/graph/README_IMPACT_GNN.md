# cosmos 영향도 GNN 학습·서빙

> 이 문서는 **소스 기업만 입력하는 U3 실험**의 기록이다. 2026-09-15 확인한 원래 Notion
> 명세(뉴스 내용에 따른 방향·강도와 GraphRAG 설명)는 `README_NEWS_IMPACT.md`를 따른다.

사용자 결정(2026-09-14): **GNN을 주모델로 사용**하고, 계수표는 비교 기준선으로 유지한다.
NER가 기사에서 찾은 기업 코드를 입력하면 GNN이 관계 이웃의 영향 크기 순위를 반환한다.
학습한 모델과 로컬 추론 API까지 구현·검증했다. 기존 Spring/프론트의 점수 계산 교체와
운영 서버 배포는 아직 하지 않았다.

## 모델이 하는 일

- 입력: 기업 코드 하나. 학습·서빙 모두 그 기업의 one-hot 값 1을 사용한다.
- 구조: 202노드, 13,608방향 엣지, 49특징, 3홉 메시지 전달, 3시드 앙상블.
- 목표: 해당 기업의 **관계 이웃** 중 같은 대상 시장 안에서 `|시장 잔차 / train 표준편차|` 순위.
- 학습 신호: 조정주가로 만든 과거 가격 이벤트. 관계 후보 간 순위 손실을 학습한다.
- 결과: 종목 순위·상대 점수·관계 유형. 실시간 가격과 GPU 없이 CPU로 서비스할 수 있다.

**뉴스 문장 내용을 조건으로 하는 모델은 아직 아니다.** 소스 기업과 대상 시장이 같으면
기사 내용이 달라도 같은 순위다. 호재/악재, 인과 전파, 다음 날 수익률을 예측하지 않는다.
여러 직접 언급 기업이 있으면 각각 요청해 소스별 결과를 표시한다. 이 모델은 여러 기업의
점수를 하나의 뉴스 점수로 합치는 규칙을 학습하지 않았다.

## 학습 및 실행

저장소 루트에서 PowerShell:

```powershell
$env:PYTHONUTF8 = '1'
uv pip install --python AI/ner/.venv/Scripts/python.exe -r AI/graph/requirements-impact.txt
AI/ner/.venv/Scripts/python.exe AI/graph/train_impact_u3.py --epochs 120 --seeds 3 --hops 3
AI/ner/.venv/Scripts/python.exe AI/graph/impact_ranker.py --source 005930 --market KOSPI --top-k 5
AI/ner/.venv/Scripts/python.exe AI/graph/serve_impact.py --port 8091
```

학습은 val 개선이 20회 없으면 조기 종료한다. 기존 `model.pt`를 덮어쓰지 않으므로 새 실험은
`--out-dir AI/graph/artifacts/새이름`을 지정한다. 해당 모델의 추론·서버 실행에는
`--bundle AI/graph/artifacts/새이름/model.pt`를 넘긴다.

기본 산출물은 `AI/graph/artifacts/impact_u3/`에 있다.

| 파일 | 내용 |
|---|---|
| `model.pt` | 가중치 3개·그래프·특징 순서·종목 순서·관계 후보를 포함한 약 3.1MB 번들 |
| `metadata.json` | 시간분할·입력 파일 해시·버전·시드별 val 선택 에폭·한계 |
| `training_history.csv` | 에폭별 loss 및 시장별 val IC |
| `evaluation.csv` | 모델별 IC·유효 이벤트 수·같은 이벤트의 짝지은 차이·세션 t |
| `gnn_scores.npy` | 저장 직전 GNN 출력. CPU 재로딩 검증용 |

아티팩트는 git에서 제외한다. 서버에는 코드·의존성과 **model.pt 하나**를 전달하면 된다.
원본 가격 CSV나 학습 데이터, 기준선 계수표는 서빙에 필요 없다. 모델 교체는 서버를 재시작한다.
기본은 `127.0.0.1:8091`이며 내부 서버 간 연결이 필요할 때만 `--host`를 지정한다.

## API 계약

`GET /health`: 모델 로딩 성공 여부·모델 버전·종목 수·그래프 기준일.

`POST /v1/impact/rank`

```json
{"sourceTicker":"005930","targetMarket":"KOSPI","topK":5}
```

```powershell
Invoke-RestMethod http://127.0.0.1:8091/v1/impact/rank -Method Post -ContentType application/json -Body '{"sourceTicker":"005930","targetMarket":"KOSPI","topK":5}'
```

응답에는 `modelVersion`, `graphAsOf`, `candidateCount`, `status`, `companies`가 있다.
각 기업은 `ticker`, `market`, `name`, `rank`, `rankScore`, `rawScore`,
`relationshipTypes`, `limitedPriceHistory`를 포함한다.

- `rankScore`: **전체 관계 후보 내 softmax 비중 × 100**. 확률·수익률·기사 중요도가 아니다.
  topK만 받으면 합이 100보다 작다. 후보가 한 개면 100이므로 높은 신뢰도로 해석하지 않는다.
- `rawScore`: GNN의 순위 logit. 음수여도 악재라는 뜻이 아니다.
- `targetMarket`: KOSPI 또는 NASDAQ. 두 시장의 점수를 섞어 한 순위로 정렬하지 않는다.
- 자기 자신과 관계가 없는 기업은 반환하지 않는다. 시장 필터는 topK보다 먼저 적용한다.
- `NO_RELATION_CANDIDATES`: 해당 시장의 후보 없음. `NO_RANKING_SIGNAL`: 후보 점수에 차이 없음.
  이를 정상적인 빈 결과로 처리하고 임의 전파 점수로 대체하지 않는다.
- 미지원 종목·시장·잘못된 topK·추가 필드는 HTTP 422. 모델이 없거나 불량이면 서버 시작 실패.

백엔드는 직접 언급 기업의 `stock_code`를 `sourceTicker`로 보내고, 반환된 `market+ticker`를
기존 company ID에 매핑하면 된다. 화면에서는 이 순위를 사용하고 기존 `DECAY=0.82` 및
`edge.score` 곱셈을 다시 적용하지 않는다. 여러 홉의 기여는 GNN 내부에서 이미 계산했다.
응답의 관계 유형은 연결 근거이며, GNN의 개별 메시지 경로를 설명하는 기여도 분해는 아니다.

## 2026-09-14 첫 U3 학습 결과

train 2017~2022 / val 2023 / test 2024-01-01~2026-09-11. 시드별 val 선택 에폭은 35/18/14.
표도 같은 train 잔차로 추정하고 같은 대상과 같은 이벤트에서 비교했다.

| 시장 | GNN val IC | GNN test IC | pair_corr test IC | GNN−표 차이 | 세션 t | 공통 test 이벤트 |
|---|---:|---:|---:|---:|---:|---:|
| KOSPI | 0.1644 | 0.1349 | 0.1042 | +0.0306 | +2.69 | 4,351 |
| NASDAQ | -0.0238 | 0.1417 | 0.0312 | +0.1105 | +2.55 | 554 |

**이 설정에서 GNN이 표보다 높았다.** 다만 NASDAQ val 이벤트가 59개이고 val IC도 음수여서
안정적인 우위를 일반화할 수 없다. 이전 U1 실험과 목표가 다르며, 옛 U3 숫자와도 직접 비교하면
안 된다. 이번에는 관계 후보를 `first_date < 2024-01-01`로 제한하고, 클리핑 경계를 2022년
말까지의 이력으로 추정하며, train 표준편차가 없는 기업을 평가 타깃에서 제외했다.

학습 기간 표준편차가 없는 종목은 **16개**다. 옛 코드의 `sigma=1` 대체는 그 기업의 정답
크기를 과소평가하며, 상관표의 미추정값 0과 맞아 점수를 부풀릴 수 있다. 다른 조건을 고정한
별도 대조에서 KOSPI pair_corr IC가 `sigma=1` 사용 시 0.2051(4,722이벤트), 제외 시
0.0915(4,393이벤트)였다. 이는 현재 모델 비교표와 별개의 감사 수치다.

## 검증과 남은 범위

- 저장한 번들을 CPU에서 재로딩해 GPU 결과와 순위가 일치했다. 최대 logit 오차 1.2e-7.
- 모델 버전 `38848187276d3333`. 회귀 테스트 8개 통과. 실제 Uvicorn HTTP 요청 30회의
  로컬 응답 시간은 중앙값 15.9ms, 최대 25.6ms였다(운영 부하 성능을 뜻하지 않는다).
- 삼성전자→KOSPI 요청의 상위 결과: SK하이닉스, 삼성SDI, 삼성에스디에스, 삼성생명, 삼성물산.
- 학습 손실 집계와 이벤트별 직접 계산의 일치, 동률/무효 후보 처리, 가격 이력이 없는
  타깃 제외, 미래 가격 변경 시 train 클리핑 불변, 저장·재로딩, API 입력 계약을 검증한다.
- 테스트: `AI/ner/.venv/Scripts/python.exe -m unittest discover -s AI/graph/tests -v`.
  API 테스트에는 개발 의존성 `httpx`가 필요하다.

그래프는 pre2024 고정 스냅샷이다. 2024년 이후 처음 생긴 관계는 후보에 들어가지 않는다.
공동언급 및 관계 구조에 val 기간 정보가 포함되어 있고 업종/지분은 역사적 as-of가 없다.
완전한 시점별 백테스트 또는 인과 검증이라고 설명하지 않는다. 실제 최신 그래프 적용 시에는
스냅샷 버전 갱신과 재평가가 필요하다. 이력 부족 기업의 구조 기반 순위는 반환할 수 있지만
그 정확도는 별도 검증 전이며 `limitedPriceHistory`로 표시한다.

현재 완료 범위는 **GNN 학습 → 모델 저장 → CPU 추론 → 내부 호출용 API**다.
Spring/프론트 연결과 운영 서버 배포는 다음 단계다. push/MR은 수행하지 않는다.

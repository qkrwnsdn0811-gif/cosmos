# 뉴스 기업 추출 모듈 (사전 매칭 + NER) — S15P21C205-45

뉴스 기사를 넣으면 언급된 상장사(KOSPI100 + NASDAQ100)를 종목코드로 돌려주는 모듈입니다.
현재 단계는 **사전 매칭 엔진(Step 2~3)** 까지이며, NER 파인튜닝·엔티티 링킹은 이후 단계입니다.

```
AI/ner/
├── build_aliases.py      companies.csv + seeds → data/aliases.csv 생성
├── matcher.py            Aho-Corasick 매칭 + 경계 규칙 + 중의성 해소 (CompanyMatcher)
├── run_sample.py         로컬 샘플 JSONL에 매처 실행, 통계·리뷰 출력
├── make_eval_set.py      수동 라벨링용 평가셋 200건 추출 (Step 8)
├── spark/
│   ├── sample_news.py    HDFS 뉴스에서 텍스트 샘플 추출 (추가 EC2에서 spark-submit)
│   └── mine_aliases.py   "이름(티커)" 패턴 채굴로 별칭 후보 수집
├── tests/test_matcher.py 규칙별 단위 테스트
└── data/
    ├── companies.csv     Step 1 산출물 (KOSPI 100 + NASDAQ 102)
    ├── aliases.csv       Step 2 산출물 (생성 파일, 직접 수정하지 말고 seeds 수정 후 재생성)
    ├── seeds/            사람이 관리하는 시드
    │   ├── nasdaq_ko.csv     NASDAQ 종목 한글명·영문 변형
    │   ├── kospi_manual.csv  KOSPI 은어·영문명·구사명·은행 자회사
    │   ├── groups.csv        그룹명·약어 (needs_context)
    │   └── blockers.txt      우선주·스포츠팀·비상장 계열사·일반단어 차단
    └── eval/eval_set_200.jsonl  수동 라벨링 대상 (gold 비어 있음)
```

## 실행

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe pandas pyarrow pyahocorasick
PYTHONUTF8=1 .venv/Scripts/python.exe build_aliases.py
PYTHONUTF8=1 .venv/Scripts/python.exe -m unittest tests.test_matcher
PYTHONUTF8=1 .venv/Scripts/python.exe run_sample.py data/sample_domestic_2025.jsonl --review 20
```

```python
from matcher import CompanyMatcher
m = CompanyMatcher.from_csv("data/aliases.csv")
r = m.match(title, body)
r.by_ticker()   # [{ticker, name_official, n_mentions, first_pos(title|lead|body), confidence, method(dict|rule), aliases}]
r.unresolved    # "삼성", "현대"처럼 계열사 없이 그룹명만 나온 경우 (사전 보강 루프 입력)
r.is_sports     # 스포츠 기사 판정 → 그룹명을 지주사로 귀속하지 않음
```

## 성능 (Step 8, 평가셋 200건, 기사×종목 단위)

| 시스템 | P | R | F1 |
| --- | --- | --- | --- |
| 사전만 (conf 1.0) | 0.918 | 0.966 | 0.941 |
| 사전 + NER v2 (conf ≥ 0.5) | 0.905 | 0.983 | 0.942 |


> **이 F1을 대외 수치로 쓰지 마세요.** 이 200건은 보강 루프에서 오답을 보고 사전을 고칠 때 쓴 바로 그 200건이라 in-sample(튜닝셋) 값이고, 홀드아웃이 없습니다. 게다가 채점 모수가 작습니다 — gold (기사×종목) 58쌍, 회사가 실제로 언급된 기사는 32건뿐이라 문서 단위 부트스트랩 95% 신뢰구간이 **[0.871, 1.000]** 입니다. 오답 1건이 F1을 약 0.8%p 움직이므로 다음 회차의 '0.942 → 0.95' 같은 비교는 노이즈와 구분되지 않습니다. 한 번도 튜닝에 쓰지 않은 200건을 따로 뽑아 한 번만 채점하는 테스트셋이 필요합니다.

정답지는 매처·사전을 보지 않은 독립 라벨링 에이전트 5개가 만들었고(`data/eval/batches/labels_*.jsonl`), 사람 검수 전입니다. 재채점: `PYTHONUTF8=1 .venv/Scripts/python.exe evaluate.py` (`--merge`로 라벨 재병합, `--no-ner`로 사전만). 보강 루프 1회전으로 F1 0.828 → 0.942 (docs/HANDOFF_20260909.md 8-5장).

## 완성 파이프라인 (Step 7)

```python
from pipeline import CompanyExtractor
ex = CompanyExtractor()                       # data/aliases.csv + models/ner-company-v2 (GPU 없으면 CPU)
out = ex.extract("news-123", title, body)
out["companies"]   # [{news_id, ticker, name, n_mentions, first_pos, confidence, method, aliases}]  ← 최종 출력
out["industries"]  # 산업 노드 언급 (기업명 없는 업황 기사용)
out["unlinked"]    # NER이 찾았지만 사전에 없는 기업 표기 → 사전 성장 후보
```

`linker.py`가 NER 표기를 종목코드로 연결합니다: 정규화(㈜·한자·공백 제거) → 별칭 완전일치 → 기관·정당·스포츠팀 제외 → 문자 유사도(≥0.84) → 미확인. 사전이 이미 결정한 구간(매칭·차단·그룹명)과 겹치는 NER 스팬은 무시하므로 사전 결과는 항상 보존됩니다. 데모: `PYTHONUTF8=1 .venv/Scripts/python.exe pipeline.py --limit 5`.

## 매칭 규칙 요약

1. **최장 일치**: `LG에너지솔루션` 안의 `LG`, `삼성전자우` 안의 `삼성전자`는 잡히지 않음.
2. **경계 규칙**: 앞 글자가 한글/영숫자면 기각(`SKT`→KT ✗). 뒤 글자가 영숫자면 기각(`KTX` ✗). 뒤가 한글이면 조사로 시작할 때만 허용(`삼성전자는` ✓, `하이브리드` ✗, `현대적인` ✗).
3. **티커**: 6자리는 `삼성전자(005930)` 괄호 문맥에서 허용. 영문 티커는 `$NVDA`, `NASDAQ: NVDA` 또는 같은 회사 이름 바로 뒤 `엔비디아(NVDA)`만 허용. `황반변성(AMD)` 같은 약어 충돌 방지.
4. **차단(blocker)**: 우선주, 야구·농구팀, 비상장 계열사(SK온, GS칼텍스), 일반 단어(하이브리드)는 스팬을 소비하고 결과에서 제외.
5. **그룹명(needs_context)**: 같은 문서에 계열사가 있으면 계열사로 귀속. 없으면 지주사 티커로 confidence 0.5 방출(LG, SK, 한화, 두산, GS, LS). 삼성·현대처럼 지주사가 없으면 `unresolved`. 스포츠 기사면 항상 `unresolved`. 영문 문서에서 2글자 약어(GS, MS, KB)는 무시.

## 2025년 샘플 결과 (2026-09-09)

| 샘플 | 문서 | 기업 언급 문서 | 비고 |
| --- | --- | --- | --- |
| 국내 2025 (mysql_domestic) | 5,000 | 22.1% | 뷰타임즈·머니투데이·헬로티 중심, 스포츠·연예 기사 다수 |
| 해외 2025 (mysql_overseas) | 2,000 | 37.1% | Simply Wall St·Motley Fool 등 미국 증시 기사 |

미확정 상위: 삼성 170, 한화 161(대부분 야구), 현대 122, LG 68. 이 로그가 Step 9 사전 보강 루프의 입력입니다.

## Step 4~6 파이프라인

```
HDFS 뉴스 원문 ──spark/batch_mentions.py──▶ sandbox/.../company-mentions/run_id=*/{data,spans,unresolved}
                                                          │ spans
                                            spark/make_bio_dataset.py ──▶ ner_corpus_v1.jsonl (문장 + 문자 오프셋 엔티티)
                                                          │ scp
                                            train_ner.py (로컬 RTX 4050, klue/roberta-base) ──▶ models/ner-company-v1
                                                          │
                                            predict_ner.py  → 사전이 놓친 기업명 후보 (Step 7 링킹 입력)
```

- **Step 4** `spark/batch_mentions.py`: 본문 대표 행만 골라 매처 적용. 결과는 실행별 고유 경로에 `errorifexists`로 저장하고 재읽기 검증 후 `manifest.json`·`_VERIFIED`를 남깁니다. 기본 출력은 `/data-lake/sandbox/news/<owner>/`이고 팀 합의 후 `--promote`로 `/data-lake/analyzed/`에 씁니다. 서버에 pip이 없어 매처는 pyahocorasick 없이 정규식 백엔드로 동작합니다(결과 동일, 0.9ms/문서).
- **Step 5** `spark/make_bio_dataset.py`: confidence 1.0 사전 매칭만 정답으로 사용, 미확정 그룹명이 있는 문장은 제외, 매칭이 전혀 없는 문서의 문장을 음성으로 25% 혼합. record_id 해시로 train/dev/test 90/5/5 분할. 로컬 검증용 동일 로직은 `make_bio_local.py`.
- **Step 6** `train_ner.py`: 토크나이저 offset mapping으로 문자 스팬을 B/I-COMPANY 토큰 라벨로 변환, fp16, batch 16, max_len 128. transformers 5.x 기준. `predict_ner.py`로 문자 오프셋 스팬을 반환합니다.

원거리 지도 라벨의 한계: 사전에 없는 기업(현대제철, LG디스플레이 등)이 음성 문장에 섞여 "기업 아님"으로 학습될 수 있습니다. 실제로 v1은 사전을 거의 복제했습니다(평가셋 200건에서 사전 밖 신규 15건). **v2**(`make_corpus_v2.py`, KLUE-NER 기관명 골드 라벨 혼합, 음성 10%, 1에폭)는 신규 129건을 찾고 포스코·Micron·현대차그룹을 잡습니다. 현재 기본 모델은 `models/ner-company-v2`입니다. 평가는 반드시 Step 8 수동 라벨 200건으로 합니다.

## 서버(추가 EC2) 작업

```bash
ssh -i J15C205T.pem ubuntu@j15c205a.p.ssafy.io
export PATH=$PATH:/opt/spark-4.2.0-bin-hadoop3/bin
cd /home/ubuntu/ner-work
spark-submit --master 'local[2]' --driver-memory 3g sample_news.py --region domestic --year 2025 --n 5000 --out /home/ubuntu/ner-work/sample_domestic_2025.jsonl
spark-submit --master 'local[2]' --driver-memory 4g mine_aliases.py --years 2023 2024 2025 2026 --out /home/ubuntu/ner-work/alias_candidates_domestic.csv
```

HDFS는 읽기만 하고, 결과는 서버 로컬 `/home/ubuntu/ner-work/`에만 씁니다. 다음 단계(Step 4 Spark 배치)에서 `/data-lake/analyzed/news/company-mentions/`에 실행별 경로로 저장할 예정입니다.

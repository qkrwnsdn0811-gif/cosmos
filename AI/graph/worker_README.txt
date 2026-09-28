# 두 번째 노트북에서 할 일

1. sentiment_worker.zip 을 풀어 짧은 경로에 둔다 (예: C:\cosmos\worker)
   - 처음 실행한 뒤에는 폴더를 옮기지 않는다

2. 준비물 확인
   - Python 3.12        py -3.12 --version
   - NVIDIA 드라이버     nvidia-smi

3. 실행 — 둘 중 하나

   PowerShell:   powershell -ExecutionPolicy Bypass -File run.ps1
   Git Bash:     bash run.sh

   진행 상황이 5만 문장마다 한 줄씩 찍힌다.

   처음 한 번은 가상환경을 만들고 라이브러리를 받는다(인터넷 필요, 약 3GB).
   두 번째부터는 바로 추론으로 넘어간다.

4. 예상 소요: GPU 로 약 1시간 40분 (231만 문장, 실측 400문장/초)
   중간에 꺼져도 괜찮다. 다시 실행하면 끝난 문장은 건너뛰고 이어서 한다.

5. 끝나면 data/labels_shard1.parquet 파일을 돌려준다.


## 무엇을 하는가

뉴스 근거 문장에 FinBERT 로 긍정/부정/중립을 매긴다.
한글이 있는 문장은 KR-FinBert-SC, 없으면 ProsusAI/finbert 로 보낸다.

두 모델의 라벨 순서가 반대라서 config.json 의 id2label 을 읽어 매핑한다.
그냥 argmax 를 쓰면 영문 감성이 통째로 뒤집힌다. 이 검증은 코드에 들어 있다.

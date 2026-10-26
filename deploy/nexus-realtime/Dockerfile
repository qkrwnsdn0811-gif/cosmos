# NEXUS 파이프라인 실행 이미지 (실시간 수집 · 밤 전체 갱신). 저장소 코드는 실행할 때 /app 으로 붙이고,
# 기사 캐시·Parquet·분석 결과(nexus/data)는 볼륨에 둔다.
# 작업 폴더가 /app/nexus 라 `python -m nexus` 는 붙인 저장소의 코드를 쓴다. 빌드 맥락은 저장소 루트다:
#   docker build -f deploy/nexus-realtime/Dockerfile -t nexus-pipeline .
FROM python:3.12-slim

WORKDIR /app/nexus
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 TZ=Asia/Seoul

# 의존성만 이미지에 굽는다 (KR-FinBERT·KPF-BERT 용 torch 는 넣지 않는다 — 관계 판별기는 모델이 없으면 규칙만 쓴다)
COPY nexus/pyproject.toml /tmp/nexus/pyproject.toml
COPY nexus/nexus /tmp/nexus/nexus
RUN pip install --no-cache-dir /tmp/nexus && pip uninstall -y nexus && rm -rf /tmp/nexus

ENTRYPOINT ["python", "-m", "nexus"]

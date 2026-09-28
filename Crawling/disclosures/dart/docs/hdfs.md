# 스냅샷과 HDFS 실행 절차

명령은 모듈 루트에서 실행한다. HDFS 단계는 접근 가능한 Hadoop 클라이언트·Java와 기존 HDFS 권한이 필요하다. `/opt/hadoop/bin/hdfs`가 기본값이며 업로더의 `--hdfs`로 다른 실행 파일을 지정할 수 있다. 설정 예시는 다음과 같다.

```bash
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
export HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop
```

## 완료 스냅샷 만들기

준비 스크립트는 `shard-*/companies/*.csv`, 대응 `_detail.jsonl`, `shard-*/raw/documents/<접수번호>.zip` 구조를 읽는다. 아래 서버 재개기로 수집하면 이 구조가 생성된다. 독립 수집기 출력을 사용할 때에도 이 구조로 대상 경로를 준비한다.

```bash
python scripts/run_dart_hdfs_service.py \
  --project . --key-file .env.dart.local --end 20260909
```

3개 shard는 51–68/69–78/79–100위이며 키의 요청 장부와 속도 제한을 공유한다. 할당량 초과는 한국시간 다음 날 00:05에 재시도하고, 반복 오류나 인증 오류는 `needs_attention`으로 남긴다. 서비스의 키는 같은 비공개 파일에서 읽으며 부모 환경의 다른 `DART_API_KEY`를 자식에게 상속하지 않는다.

목록·본문·원본 ZIP이 모두 갖춰진 정상 공시와 명시적 원천 예외가 확인되면 재개기가 최종 스냅샷 생성과 HDFS 검증 업로드까지 진행한다. 기존 3개 접수번호의 원천 오류와, 2026-09-15 재다운로드로 동일 CRC 손상을 확인한 `20200305000085`를 구분해 보존한다. 추가 예외는 코드에 기록된 ZIP SHA-256과 정확히 일치할 때만 적용한다. 이 예외 문서는 정상 수집 건수에 포함하지 않으며 다른 파싱 오류는 완료로 간주하지 않는다.

## 기존 스냅샷 이후 누락분만 준비

`scripts/prepare_dart_recovery_delta.py`는 검증한 기존 HDFS `metadata/document_index.jsonl`과 로컬 수집 결과를 접수번호로 비교한다. 기존 원본을 변경하지 않고 아직 게시되지 않은 정상 본문·ZIP만 별도 스냅샷으로 준비한다.

```bash
python scripts/prepare_dart_recovery_delta.py \
  --source /path/to/historical/parallel-3 \
  --baseline-index /path/to/verified/document_index.jsonl \
  --baseline-sha256 BASELINE_INDEX_SHA256 --baseline-count BASELINE_DOCUMENT_COUNT \
  --output /path/to/new-recovery-snapshot --report /path/to/recovery-report.json \
  --end 20260909 --key-file /private/path/opendart.env
```

baseline index는 HDFS의 `ready.json`과 `checksums.json`의 해시 계약으로 먼저 검증한다. 보고서의 `status=ready`, 포함 접수번호 집합·건수, 제외 사유를 확인한 후 기존 업로더로 새 경로에 적재한다. `ready_with_exclusions`는 검토가 필요한 부분 결과다. 준비만 완료한 것을 HDFS 저장 완료로 취급하지 않는다.

수동으로 특정 경계를 패키징할 때:

```bash
python scripts/prepare_dart_hdfs_snapshot.py \
  --source output/opendart-rank51-100-20160101-20260909/parallel-3 \
  --output output/snapshots/my-snapshot \
  --begin 20160101 --end 20260909 --key-file .env.dart.local

python scripts/upload_dart_hdfs_snapshot.py \
  --source output/snapshots/my-snapshot \
  --destination /datasets/opendart/snapshots/my-snapshot \
  --report output/my-snapshot-upload-report.json
```

스냅샷은 `data/*.jsonl.gz`(회사별 본문), `raw/documents-part-*.tar`(원본 ZIP), `metadata/document_index.jsonl`, 회사 목록·미수집/원천 예외, `manifest.json`, `checksums.json`, `ready.json`으로 구성된다. 본문 한 줄은 접수번호 하나다. TAR는 같은 문서의 보존본이므로 본문과 합쳐 문서 수를 세지 않는다.

패키징은 캡처 시점 byte 경계의 완전한 JSONL 줄만 사용한다. `ready.json`은 로컬 준비 완료 표시다. HDFS 완료는 업로드 보고서 `uploaded_verified` 또는 `already_verified`와 대상 파일 크기·SHA-256 검증을 확인해야 한다. 기존 최종 스냅샷을 덮어쓰지 않는다.

## 기존 CSV·본문 ZIP import

이 도구는 회사별 `<회사>.csv`와 `<회사>_detail.jsonl`을 담은 기존 수집 묶음을 받는다. DART의 `document.xml` 개별 ZIP을 받는 도구가 아니다. UTF-8/CP949 및 Unicode filename extra field를 검증하고 경로 탈출·암호화·심볼릭 링크를 거부한다.

```bash
python scripts/scan_dart_zip_credentials.py \
  --archive /path/to/dart.zip --report output/import-credential-check.json \
  --key-file .env.dart.local

python scripts/import_dart_zip_hdfs.py \
  --archive /path/to/dart.zip --output output/imports/my-import \
  --credential-scan-report output/import-credential-check.json \
  --destination /datasets/opendart/imports/my-import \
  --report output/my-import-upload-report.json
```

사용한 키가 여러 개면 스캐너의 `--key-file`을 반복한다. 이 검사는 제공한 키와 로컬 `.env.dart*.local`의 **알려진 값**을 대조하는 방식이며 모든 종류의 비밀을 탐지한다고 보장하지 않는다. 하나 이상의 알려진 키 패턴, archive 해시 일치, CRC 통과, 일치 0건이 필요하다.

import는 `rcept_dt`를 기준으로 2016년 이전/이후 본문을 나누고 원래 본문을 보존한다. CSV에만 있고 본문이 없는 건은 저장 완료 건수가 아니다. `--prepare-only`로 HDFS 업로드 없이 패키지 준비까지만 실행할 수 있다.

## systemd 예시

`deploy/opendart-collector.service.example`의 경로·계정·기간·Hadoop 환경을 설치 환경에 맞춘 뒤 사용한다. 예시는 자동 설치·활성화되지 않는다. 실행 계정이 코드·출력과 개인 키 파일을 읽고 HDFS에 쓸 수 있어야 한다. 운영에서 사용하는 수집 출력/상태는 Git 밖에 보존한다. 기존 서비스의 상태·디렉터리를 덮어쓰거나 병렬로 다른 복제 서비스를 띄우지 않는다.

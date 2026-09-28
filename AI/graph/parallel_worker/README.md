# Cosmos 뉴스 후보 선별 보조 노트북

이 패키지는 이 노트북에 배정된 뉴스–기업 쌍만 기존 B 모델로 **잠정 채택·잠정 제외·보류** 분류합니다. LLM 호출이나 모델 재학습은 하지 않습니다. 결과는 검증 완료 라벨이나 지식그래프가 아니며 서비스에 자동 반영되지 않습니다.

## 필요한 준비

- NVIDIA GPU와 정상 작동하는 드라이버가 필요합니다. `nvidia-smi`로 장치를 확인하세요. GPU가 없거나 CUDA 실행에 실패하면 중단하며 CPU로 대신 실행하지 않습니다.
- **Python 3.12 정식 설치본**과 `venv`·`pip`가 필요합니다. 이 실행 파일은 Python이나 NVIDIA 드라이버를 설치하지 않습니다. Linux에서 `venv`가 빠져 있으면 해당 배포판의 Python 3.12 venv 패키지를 먼저 설치해야 합니다.
- 첫 실행은 `.venv`를 만들고 CUDA PyTorch와 필요한 Python 라이브러리를 설치하므로 인터넷과 추가 디스크 공간이 필요합니다. 입력·모델 외에도 가상환경과 결과 저장 공간을 확보하세요.
- WSL은 Linux Python과 WSL GPU 지원이 필요합니다. Windows와 WSL 사이에서 `.venv`나 실행 중인 `output`을 공유하지 마세요. Git Bash에서는 Windows Python을 사용할 수 있습니다.
- 노트북을 전원에 연결하고 절전·자동 잠금을 구분해 설정하세요. 절전·종료 중에는 계산하지 않습니다. 서로 다른 노트북에 **동일 배정 패키지를 중복 실행하지 마세요.**

## 한 번에 실행

패키지를 압축 해제한 폴더에서 Linux·WSL·Git Bash는 다음을 실행합니다.

압축은 `C:\cosmos\pc2`처럼 짧은 **최종 실행 경로**에 폴더 전체를 풀어주세요. 첫 실행 전에는 패키지를 옮겨도 됩니다. 첫 실행 후에는 동결된 재개 기록에 절대 경로가 들어가므로 폴더 이름이나 위치를 바꾸지 마세요.

```bash
bash run.sh
```

Windows PowerShell에서는 다음을 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

최초 설치, 패키지 파일 해시 확인, CUDA 점검을 거쳐 배정된 데이터를 처리합니다. 콘솔은 진행률을 표시하며, 자세한 로그는 `logs/`, 결과와 재개 상태는 `output/`에 저장합니다. 같은 명령을 다시 실행하면 완료된 묶음은 재사용합니다. 콘솔을 닫으면 계속 실행된다고 보장하지 않습니다.

Python 위치를 지정해야 한다면 실행 전에 설정하세요.

```bash
export COSMOS_WORKER_PYTHON='/path/to/python3.12'
bash run.sh
```

```powershell
$env:COSMOS_WORKER_PYTHON = 'C:\Python312\python.exe'
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

선택적으로 제공된 `python/python.exe`가 있다면 탐색하지만, `venv`·`pip`가 없는 Windows 임베디드 배포판은 자동 지원하지 않습니다.

## 확인·중단·재개

분류 전에 패키지·모델·GPU만 확인하려면:

```bash
bash run.sh --check
```

검사용으로 64쌍만 처리하고 다음 64쌍을 이어서 처리하려면:

```bash
bash run.sh --max-pairs 64
bash run.sh --max-pairs 64
```

`--max-pairs`는 **이번 실행에서 새로 저장할 쌍 수**입니다. 전체 한도가 아닙니다. 이후 `bash run.sh`를 실행하면 남은 배정분을 처리합니다. Windows도 `run.ps1` 뒤에 같은 옵션을 붙입니다.

안전하게 일시 중단하려면 실행 중 다른 터미널에서 `output/STOP` 파일을 만들거나 실행 콘솔에서 Ctrl+C를 누르세요. 이미 저장된 묶음은 유지되며 미완료 묶음은 다음 실행에서 다시 계산됩니다. STOP이 있으면 일반 실행으로 지우지 않습니다.

```bash
touch output/STOP
bash run.sh --resume-after-stop
```

```powershell
New-Item -ItemType File -Path .\output\STOP -Force
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 --resume-after-stop
```

강제 종료·전원 차단 후에도 SQLite에 커밋된 묶음부터 재개합니다. 파일 손상이나 입력·모델·코드 변경을 발견하면 중단합니다. 검사를 우회하거나 기존 `output`을 다른 패키지에 복사해서 이어 돌리지 마세요.

## 결과 전달

`output/status.json`에 `state: provisional_complete`와 `complete: true`가 있어야 배정분 처리 완료입니다. `output/provisional_receipt.json`과 `output/exports/`가 최종 결과이며, `output/` 폴더 전체를 담당자에게 전달하세요. 입력·모델·코드 원본은 수정하지 않습니다.

담당자 PC로 복사한 `output/`은 결과 병합용입니다. 다른 경로에서 그 복사본을 재개하지 마세요. 재개는 원래 실행했던 노트북의 동일한 패키지 경로에서 수행합니다.

`include`와 `exclude`도 잠정 판정입니다. 보류·입력 초과·추론 실패를 무관한 뉴스로 간주하지 않습니다. 이 단계에서 기업 관계, 원문 근거, 감성 배지는 생성하지 않습니다.

## 패키지 구성과 설치 범위

```text
run.sh / run.ps1 / bootstrap.py / requirements.txt / README.md
package_manifest.json
app/                 원본 Python 코드와 모델 영수증
model/               기존 B manifest와 checkpoint
input/               이 노트북에 배정된 입력 manifest·protocol·parts
.venv/               첫 실행에서 생성하는 독립 Python 환경
logs/                설치 이후 사전 확인과 작업 로그
output/              실행 중 생성하는 결과·SQLite·STOP
```

파일 SHA-256은 전송 손상을 검사합니다. `package_manifest.json` 자체의 진위까지 증명하는 전자서명은 아닙니다. 신뢰할 수 있는 팀원이 제공한 패키지를 사용하세요.

고급 검사용 `--skip-install`은 현재 Python이 정확한 고정 버전을 모두 갖춘 경우에만 설치를 생략합니다. GPU·파일·모델 검증은 그대로 수행합니다. 일반 실행에서는 이 옵션이 필요 없습니다.

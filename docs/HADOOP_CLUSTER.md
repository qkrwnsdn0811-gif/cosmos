# COSMOS Hadoop 클러스터 구축 기록

## 1. 문서 상태

**확인 시각: 2026-09-14 13:39 KST / 04:39 UTC.** AWS Worker 3대는 모두 `running`이며 인스턴스·시스템 상태 검사 `ok`다. Master와 Worker 3대는 같은 Tailnet에서 상호 `direct` 연결을 사용한다. Master DataNode를 안전하게 decommission한 뒤 Worker 3대만으로 HDFS/YARN을 구성했고, 세 Worker의 순차 재부팅과 실제 Spark 분산 작업까지 통과했다.

AWS 리소스 정보는 읽기 전용 API로 재확인한 결과다. OS 내부 설치 완료와 Master 주소는 구축 작업 보고를 반영했다. 아래 검증 표에서 설치 완료와 클러스터 동작 검증을 구분한다.

논리 구조와 데이터 계약은 [분산 데이터 인프라 설계서](./infrastructure-design.md), [데이터베이스 설계서](./database-design.md)를 따른다. 이번 구축에서는 현재 AWS Free Plan 지원 유형과 적재 용량을 반영하여 Worker를 `m7i-flex.large`, 데이터 EBS를 대당 `200 GiB`로 선택했다.

## 2. 서버 역할과 용량

| 서버 | 역할 | 배치 |
| --- | --- | --- |
| SSAFY 메인 EC2 | Nginx, Spring Boot, PostgreSQL, Redis, 웹 CI/CD | 기존 서비스 유지 |
| SSAFY 추가 EC2 | HDFS NameNode·SecondaryNameNode, YARN ResourceManager, Spark Driver | Master Tailscale `100.117.115.44`, Worker 3대와 direct 연결 |
| AWS Worker 1~3 | HDFS DataNode, YARN NodeManager, Spark Executor 실행 대상 | 같은 서울 리전 VPC·AZ, Tailnet 가입 및 Hadoop daemon 등록 완료 |

Collector, Kafka, HDFS Writer, AI 분석과 RDB Loader의 실제 배포 및 데이터 처리 작업은 각 구현 단계에서 별도로 검증한다. Worker 생성만으로 전체 분석 파이프라인이 완성되는 것은 아니다.

각 Worker의 생성된 AWS 사양과 클러스터 설정은 다음과 같다.

| 항목 | 값 |
| --- | --- |
| 인스턴스 | `m7i-flex.large`, x86_64, 2 vCPU, 8 GiB RAM |
| OS | Canonical Ubuntu Server 24.04 LTS amd64 |
| 루트 EBS | gp3 30 GiB |
| 데이터 EBS | gp3 200 GiB |
| gp3 기본 성능 | 3,000 IOPS, 125 MiB/s, 추가 성능 구매 없음 |
| HDFS 복제 계수 | `2` |
| 클러스터 주소 | Hadoop 광고 주소는 아래 Tailscale IPv4 사용, 이름은 `/etc/hosts` 관리 매핑으로 보조 |

설치 스크립트는 Hadoop 3.5.0/Java 17, Spark 4.2.0/Java 21을 사용한다. `install-worker.sh`의 설치 완료는 Hadoop daemon이 클러스터에 가입했다는 의미가 아니다. 설정·포트·서비스 계약은 [configuration-contract.md](../deploy/hadoop/templates/configuration-contract.md)에 있다.

마이그레이션 기준선은 파일 `43,567`개, 논리 바이트 `109,587,192,220` bytes(`102.06 GiB`)다. 복제 계수 2의 블록 payload는 약 `204.12 GiB`로, `100 GiB × 3 = 300 GiB` 구성이라면 약 68.0%를 사용해 설계서의 65% 확장 기준을 넘는다. 따라서 다음 확장 단계인 `200 GiB × 3 = 600 GiB`로 시작했다. 원본 복제본만 고려하면 원시 EBS 용량의 약 34.0%이며, 체크섬·파일시스템 예약 공간·정제 및 분석 결과·로그·Spark 임시 공간은 별도 여유가 필요하다.

600 GiB의 HDFS 물리 용량에서 복제 계수 2와 20% 운영 여유를 적용한 논리 데이터 기준은 약 240 GiB이다. 파일시스템 예약 공간과 Spark 임시 공간을 함께 사용하면 실제 한도는 더 낮다.

## 3. AWS 조회 결과와 현재 리소스

계획·quota는 생성 전 조회했으며, 사용 리소스와 상태는 위 확인 시각에 다시 조회했다.

| 항목 | 조회 결과 |
| --- | --- |
| 계정 | `853377042824` |
| 계정 계획 | `FREE`, `ACTIVE` |
| 남은 크레딧 | API 표시 USD 180.00, 당일 생성 이후 사용료 반영 지연 가능 |
| Free Plan 만료 예정 | 2027-03-11 04:46 UTC, 크레딧 소진 시 더 일찍 종료 |
| 리전 | `ap-northeast-2` |
| Standard On-Demand vCPU quota | 32 vCPU, 3대에 6 vCPU 필요 |
| COSMOS EC2 현재 사용 | running 3대, 총 6 vCPU |
| gp3 스토리지 quota | 50 TiB, 현재 COSMOS 합계 690 GiB |
| COSMOS EBS 현재 사용 | in-use 6개, 모두 암호화 gp3 |
| 기본 VPC | `vpc-0e650c3c7c22670a5`, `172.31.0.0/16` |
| 실제 같은 AZ 배치 | `ap-northeast-2a`, AZ ID `apne2-az1`, `subnet-0eb1f6f509ed30c81`, `172.31.0.0/20` |
| 서브넷 여유 IP | 4,088개, 공인 IPv4 자동 할당 활성 |
| 기본 경로 | `0.0.0.0/0` → `igw-0726c72a811aa0b1d`, active |
| 기본 라우트 테이블 | `rtb-0364fb761cfa338df` |
| m7i-flex.large 제공 AZ | 서울 `2a`, `2b`, `2c`, `2d` 모두 조회됨 |

`t3.large`는 이 계정에서 조회한 Free Tier 적격 목록에 없었다. 같은 2 vCPU/8 GiB의 `m7i-flex.large`는 적격 목록에 포함되어 있어 계정을 Paid Plan으로 전환하지 않는 구성으로 선택했다. gp3 역시 Free Plan 지원 유형이다. 이는 무료 고정 사용량을 보장한다는 뜻이 아니라 사용 비용을 크레딧으로 충당한다는 뜻이다. [AWS EC2 Free Tier 기준](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html)

현재 3대 생성에 필요한 CPU와 스토리지는 확보되었다. 새 계정으로 이전할 때에는 새 계정의 계획·잔액·quota·AMI·가용 용량을 다시 확인한다.

### 3.1 AMI 확인

- 공개 SSM 파라미터: `/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id`
- 조회한 AMI: `ami-086a43496cb46286c`
- 이름: `ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-20260904`
- 소유자: Canonical `099720109477`
- 상태: `available`, 아키텍처: `x86_64`
- `FreeTierEligible=true`, 별도 Marketplace 제품 코드 없음
- 루트 장치: `/dev/sda1`; 생성 시 위 계획의 30 GiB gp3로 지정

재구축 시에는 동일 파라미터를 다시 조회하고 AMI 소유자·아키텍처·지원 여부를 재검증한다. `current` 파라미터 값은 시간이 지나면 변경될 수 있다.

### 3.2 생성된 Worker와 EBS

3대는 2026-09-14 11:25 KST에 생성되었고 공통 AMI는 `ami-086a43496cb46286c`이다. 공인 IP는 인스턴스 중지·시작 후 달라질 수 있다.

| 이름 | 인스턴스 ID | VPC 사설 IP | 확인 시 공인 IP | Tailscale |
| --- | --- | --- | --- | --- |
| cosmos-worker-1 | `i-04eba7bc81cd7cac5` | `172.31.5.28` | `54.180.162.37` | `100.104.188.115`, 승인·direct 확인 |
| cosmos-worker-2 | `i-01f33df83b4776b27` | `172.31.4.120` | `3.36.58.185` | `100.68.206.1`, 승인·direct 확인 |
| cosmos-worker-3 | `i-053d5c986c716db5f` | `172.31.1.172` | `3.34.196.157` | `100.124.47.107`, 승인·direct 확인 |

| Worker | 루트 30 GiB | 데이터 200 GiB |
| --- | --- | --- |
| 1 | `vol-03011bbe800e70fd1` | `vol-0e704e843f87939e2` |
| 2 | `vol-00abc1aaa41b9f83d` | `vol-035c09021d78b4e0c` |
| 3 | `vol-0fc869cfe51be3ced` | `vol-0efdd98a1752677ae` |

모든 볼륨은 암호화 gp3, 3,000 IOPS/125 MiB/s이다. 루트는 `/dev/sda1`에 `DeleteOnTermination=true`, 데이터는 `/dev/sdf`에 `DeleteOnTermination=false`로 연결되었다. 따라서 **인스턴스를 종료해도 데이터 EBS 3개는 남아 비용이 계속 발생한다.** 이전 후 검증과 보존 결정을 마친 다음 구계정 데이터 볼륨을 별도로 정리해야 한다.

인스턴스·볼륨·보안 그룹에는 `Project=COSMOS`, `Component=hadoop-worker`, `ManagedBy=cosmos-infrastructure`가 설정되어 있다. 3대 모두 IMDSv2 토큰 필수, hop limit 1이며 인스턴스 IAM 프로파일은 없다.

### 3.3 보안 그룹 확인

공통 보안 그룹은 `sg-02490c56e69b42412` / `cosmos-hadoop-workers`이다.

| 방향 | 규칙 | 목적 |
| --- | --- | --- |
| Inbound TCP 22 | 관리 출발지 2개의 `/32` 주소만 허용 | 초기 SSH 관리 |
| Inbound UDP 41641 | VPC `172.31.0.0/16`, Master 공인 IP `/32` | VPC 내부 Worker와 Master의 암호화된 Tailscale direct 전송 |
| Outbound | IPv4 전체 허용 | 패키지 설치 및 외부 연결 |

Hadoop·YARN·Spark 포트의 공인 Inbound 규칙은 없다. 각 호스트 UFW는 `tailscale0`에서 inventory의 정확한 peer IP와 포트만 허용하며 Tailscale을 `nodivert` 모드로 전환했다. Master와 Worker 사이 및 Worker 상호 간 `direct` 연결을 확인했다. Tailscale이 발견하는 실제 endpoint는 시점과 경로에 따라 VPC 사설 주소 또는 공인 주소일 수 있고 바뀔 수 있으므로, 재부팅·계정 이전·네트워크 변경 뒤 다시 확인한다.

## 4. 비용과 크레딧 기준

서울 리전, Linux Shared On-Demand 가격을 AWS Price List Query API로 2026-09-14에 조회했다. 할인·크레딧 차감 전의 USD 가격이다.

| 항목 | 조회 단가 | 수량 | 730시간 월 환산 |
| --- | ---: | ---: | ---: |
| m7i-flex.large | $0.11771 / 대·시간 | 3대 | $257.78 |
| gp3 기본 용량 | $0.0912 / GiB·월 | 690 GiB | $62.93 |
| 사용 중 공인 IPv4 | $0.005 / 개·시간 | 3개 | $10.95 |
| 합계 | | | **$331.66** |

Price List 상품 식별자는 EC2 `G8QSFBSEDGM5RXM8`, gp3 `MTK7D9SGKGYR3JD6`, IPv4 `ZKBHEVDXYBRCKFQ8`이다. EC2·gp3는 `AmazonEC2`, IPv4는 `AmazonVPC` 서비스의 서울 리전 상품을 조회했다. [EC2 요금](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS 요금](https://aws.amazon.com/ebs/pricing/), [IPv4 요금](https://aws.amazon.com/vpc/pricing/)

상시 가동 시 하루 약 **$10.90**, 20일 약 **$218.08**, **3주(504시간) 약 $228.98**이다. 현재 $180 크레딧은 이 세 항목만으로 약 **16.5일**에 해당하므로 3주 상시 운영을 충당하지 못한다. 다른 팀 AWS 계정으로 Worker를 이전하는 아래 절차는 이 운영 기간을 이어가기 위한 계획이며, 새 계정의 잔액·권한과 이전 검증이 완료되어야 비용 문제를 해결한 것으로 본다. 전송료, EBS 스냅샷, 추가 저장 용량, 다른 AWS 서비스 사용과 세금은 포함하지 않았다. EBS 일할 계산은 실제 청구 월 길이에 따라 소폭 달라진다.

| 3주 기본 비용 구성 | 계산 | 금액 |
| --- | --- | ---: |
| EC2 | 3 × 504h × $0.11771 | $177.98 |
| gp3 | 690 GiB × $0.0912 × 504/730 | $43.45 |
| 공인 IPv4 | 3 × 504h × $0.005 | $7.56 |
| 합계 | 할인·전송·스냅샷 제외 | **$228.98** |

Free Plan은 크레딧을 모두 소진하거나 계획 기간이 끝나면 계정이 종료되어 리소스 접근을 잃는다. 자동으로 Paid Plan으로 전환해 계속 운영하는 구성은 사용하지 않는다. 크레딧 고갈 전에 최종 산출물과 NameNode 메타데이터 보존을 완료해야 한다. [AWS 계정 계획 설명](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)

데이터 처리와 HDFS 접근이 없는 기간에 Worker를 중지하면 EC2 사용 비용을 줄일 수 있지만 EBS 비용은 계속 발생한다. Worker 전체가 중지된 동안에는 HDFS 데이터 읽기·쓰기도 불가능하다. 운영 중지 시간은 데이터 수집·배치 일정과 함께 결정한다.

## 5. 네트워크와 운영 자격 증명

- Master와 Worker 3대의 Tailscale 가입·상호 direct 접근을 검증했고 Hadoop 광고 주소를 Tailscale IPv4로 확정했다. 발견된 endpoint 종류는 고정된 아키텍처 계약이 아니며, 별도 AI 서버가 HDFS를 직접 사용하면 같은 검증 대상에 추가한다.
- HDFS·YARN·Spark 포트는 공용 인터넷에 공개하지 않는다. UFW는 `tailscale0`의 inventory peer와 역할별 포트만 허용한다.
- 현재 네 노드 사이 direct 연결을 확인했다. 실제 endpoint가 VPC 사설 주소인지 공인 주소인지는 시점별로 다시 확인하며, 계정 이전 때에는 새 peer 경로와 과금 조건도 다시 검증한다.
- 공인 IPv4는 설치·관리 및 사설망 연결에 사용하며 Hadoop의 광고 주소로 사용하지 않는다.
- Root 자격 증명은 운영 배포에 사용하지 않는다. 계정 초기 준비 후 서울 리전과 COSMOS 리소스 태그로 제한한 IAM 자격 증명을 사용한다.
- AWS 키, SSH 개인키, Tailscale 인증 키 및 가입 URL은 저장소에 기록하지 않는다.

## 6. 구축 후 검증 기록

완료한 항목은 날짜·실제 결과·추가 제한을 함께 기록한다.

| 검증 항목 | 상태 | 확인할 결과 |
| --- | --- | --- |
| AWS 계정·지원 유형·AMI·quota·기본 네트워크 조회 | 완료 | 위 사전 조회 결과 |
| 제한된 배포용 IAM 구성 | 구성 보고됨 | 구축 담당자가 지역·태그 제한 정책 관리, 본 문서에서는 정책 자체 재검증 안 함 |
| AWS Worker 3대 생성 | 완료 | 2026-09-14 11:34 KST API: 3대 running, instance/system status ok, 예정 이벤트 없음 |
| AWS 볼륨·태그·보안 그룹 | 완료 | 암호화 gp3 6개/690 GiB, 태그·포트 규칙·IMDSv2 확인 |
| OS·Java·Hadoop·Spark 설치 | 완료 보고됨 | 구축 작업 보고 및 설치 스크립트 기준, daemon 참여는 별도 |
| Tailscale 가입 및 통신 | 완료 | 4대 승인, 네 노드 상호 direct 확인; 발견 endpoint는 시점에 따라 사설 또는 공인 주소일 수 있음 |
| 데이터 EBS 마운트 | 완료 | 3대를 한 대씩 재부팅한 뒤 boot ID 변경, `/data` ext4 UUID 마운트 및 서비스 자동 복구 확인 |
| HDFS 구성 | 완료 | `state.json=finalized`, live DataNode는 Worker 3대만 존재, replication 2, missing/corrupt/low-redundancy/pending-reconstruction 0 |
| YARN 구성 | 완료 | RUNNING NodeManager 3대, 각 5,120 MiB·2 vCore, 컨테이너 0에서 가입 확인 |
| 파일 읽기·쓰기·복제 | 완료 | 100,000행 입력 업로드·다운로드 SHA-256 일치, 7개 블록 모두 실제 live replica 2개 |
| 분산 Spark 작업 | 완료 | Spark 4.2.0/YARN `application_1789354459696_0001`, Worker 1·2·3이 각 1개 파티션 처리 |
| 기존 HDFS 원본 보존·복제 | 완료 | 전환 전후 기준선 43,567개/109,587,192,220 bytes 유지, 전체 namespace fsck 정상 |
| 재시작 후 서비스 복구 | 완료 | 3대 순차 재부팅 뒤 Tailscale·DataNode·NodeManager·EBS 마운트 정상, HDFS/YARN 각 3대 복귀 |
| NameNode·설정 백업 | 완료 | Master 백업과 운영 워크스테이션 외부 복사본의 SHA-256 일치 확인 |
| 기존 HDFS Writer | 의도적으로 중지 | 기존 `news-hdfs-sync.timer`/service 오류를 자동 재개하지 않음; 별도 보수·쓰기 검증 후 재개 |

마이그레이션 백업은 Master `/var/backups/cosmos-hadoop/20260914T025006.381704Z-migration`과 운영 워크스테이션 `~/.aws/cosmos-cluster/backups/20260914T025006.381704Z-migration`에 보존한다. NameNode archive SHA-256은 `4868923c65782b6ce725b1eb51c892150f5a433a5a205e68ffb2b1ac597aef31`, configuration archive SHA-256은 `2b0e0886ce6e9fc3e9fc7f879ae61032a89a84f6820105fcadd1da9af2f43ba7`이다.

최종 end-to-end 검증은 `hdfs://100.117.115.44:9000/cosmos-verification/20260914T043430Z-4179faa2f94f4a79b81ae3cc70b98616`에 입력과 Spark 결과를 만들었다. 따라서 위 `43,567`개와 `109,587,192,220` bytes는 **검증 artifact 생성 전 원본 기준선**이며, 검증 후 현재 전체 파일 수·크기로 표기하지 않는다. 성공 evidence는 Master `/var/lib/cosmos-hadoop-migration/verification-success-20260914T043500Z.json`과 운영 워크스테이션 `~/.aws/cosmos-cluster/verification-success-20260914T043500Z.json`에 있고 SHA-256은 `d183ba9d684bb36dba2e359ad5a08bbb054243b51a92994a24d41f45e6e88079`다. 입력 SHA-256은 `fcaf25deb807418aa9ba7bd0395078d4aa529dc2052f190dab00a2198fe43e3f`이며, Spark 결과를 다시 읽은 뒤에도 전체 클러스터 위험 지표가 0임을 확인했다. 검증 artifact를 포함한 전체 namespace의 사후 fsck는 43,578개 파일, 109,601,679,157 bytes, 평균 복제 2.0으로 `HEALTHY`였다.

첫 검증은 Master의 기존 `/data/spark`가 `root` 전용 권한이어서 Driver 임시 디렉터리를 만들지 못해 종료됐다. 실패 evidence는 `/var/lib/cosmos-hadoop-migration/verification-failed-20260914T043300Z.json`에 보존했고, 관리 대상 Spark 디렉터리 권한을 수정한 뒤 새 경로로 재실행해 성공했다.

웹 배포는 기존 Jenkins 흐름으로 유지하고, Hadoop의 설치·설정 변경, Spark 작업 배포와 배치 실행은 웹 컨테이너 배포와 구분한다. 최종 서비스 연결은 성공한 집계 결과를 RDB Loader가 PostgreSQL에 게시하고 API가 `PUBLISHED` snapshot만 조회하는 계약을 따른다.

## 7. 다른 팀 AWS 계정으로 Worker 이전

이 절차는 현재 AWS Worker 3대가 정상적인 HDFS/YARN 클러스터로 검증된 다음 적용한다. **SSAFY Master의 NameNode·ResourceManager와 HDFS 네임스페이스를 유지하고 DataNode/NodeManager만 교체한다.** NameNode를 새로 format하거나 기존 NameNode 메타데이터·cluster ID를 교체하지 않는다. 현재 문서 시점에는 이 계정 이전을 실행하지 않았다.

### 7.1 일정·크레딧 기준

- 생성 시각은 2026-09-14 11:25 KST이다. **D+14인 2026-09-28 11:25 KST 이전에 구계정 Worker와 보존 불필요 EBS 정리를 마치는 것**을 목표로 한다.
- 새 계정 권한·잔액·지원 사양·quota 확인과 설치 준비는 D+12 이전에 시작한다. 잔액 표시가 **$30 이하이면 이전 절차를 진행 중이어야 하며, $25까지 기다려 처음 시작하지 않는다.** 전송량이 크거나 크레딧 반영이 늦으면 더 앞당긴다.
- 날짜와 잔액 중 먼저 도달하는 기준으로 움직인다. 하루 $10.90 수준에서 $25~30은 2~3일의 기본 운영 비용에 불과하며 이전 전송비도 별도로 든다.
- 새 계정에도 독립적으로 크레딧과 운영 종료 시각을 확인한다. 계정을 Paid Plan으로 자동 전환하거나 추가 크레딧 적립을 이미 받은 것으로 가정하지 않는다.

### 7.2 이전 준비

1. NameNode의 일관된 `fsimage`/`edits` 백업, 기존 설정·호스트 목록·원본 파일 수/크기·HDFS 건강 상태를 기록한다. 대규모 배치는 완료시키거나 재실행 가능한 체크포인트를 남긴다.
2. 새 계정에 같은 사양과 용량의 Worker 3대를 준비한다. 이름과 Tailscale 주소는 기존 3대와 겹치지 않게 지정한다. 같은 물리 AZ가 필요하면 `ap-northeast-2a` 이름만 비교하지 말고 현재 **AZ ID `apne2-az1`**과 비교한다. AZ 이름은 계정마다 다를 수 있다. [AWS AZ ID 설명](https://docs.aws.amazon.com/ram/latest/userguide/working-with-az-ids.html)
3. 새 Worker를 **기존 Master와 같은 Tailnet**에 가입시키고 승인한다. Master↔새 Worker, 기존↔새 Worker, 새 Worker 상호 간 주소 해석·방화벽·통신을 확인한다. `direct` 여부와 실제 발견 endpoint도 기록한다. endpoint는 VPC 사설 주소나 공인 주소가 될 수 있으며, 동일 AZ여도 다른 VPC·계정 사이에 사설 경로가 저절로 생기는 것은 아니다.
4. Hadoop/Spark/Java 버전과 계정·디렉터리 권한을 맞춘다. 새 DataNode는 빈 데이터 경로에서 기존 NameNode에 가입시킨다. 기존 Worker의 DataNode ID가 들어 있는 저장 디렉터리를 그대로 복제해 동시에 기동하지 않는다.
5. `configure-cluster.py`와 `configure-firewall.py`의 `account-transition` 모드는 새 `workers` 3대와 기존 `retiring_workers` 3대를 동시에 허용한다. `configure-cluster.py`의 현재 `final` 모드는 Master와 기존 3대를 exclude 목록에 **한 번에** 넣는다. 이 모드를 사용하면 기존 DataNode 3대를 모두 실행한 채 세 노드가 전부 `Decommissioned`가 될 때까지 기다린 다음에만 서비스를 중지한다. 이 동안 방화벽은 `account-transition`을 유지하고, 기존 세 서비스를 중지한 뒤 `configure-firewall.py final`로 기존 peer 규칙을 제거한다. 한 대씩 순차 제외하려면 현재 `final` 출력을 그대로 사용하지 말고, 단계별 exclude 파일과 각 단계의 검증 조건을 별도로 검토·적용해야 한다.

### 7.3 6대로 합류·복제·균형 조정

1. 기존 3대가 계속 실행되는 상태에서 Master의 DataNode/NodeManager 허용 목록에 새 3대를 추가하고 설정을 갱신한다. HDFS `live DataNode=6`, YARN `RUNNING NodeManager=6`을 확인한다.
2. 데이터 경로별 복제 계수가 2인지 확인한다. 기본 복제 설정을 바꿔도 기존 파일의 복제 계수는 자동 변경되지 않는다. 필요한 경로만 복제 2로 맞추고 완료를 기다린다.
3. 새 Worker로 데이터가 이동하도록 복제와 HDFS Balancer를 수행하되 대역폭·디스크 사용량·크레딧을 관찰한다. **Balancer 완료만으로 기존 3대를 종료하지 않는다.** 정상 복제본 일부가 여전히 구계정에만 있을 수 있다.
4. 이전 중 missing/corrupt block이 발생하거나 새 Worker가 불안정하면 구 Worker를 유지하고 문제부터 해결한다.

### 7.4 기존 3대 decommission

1. 현재 자동화의 `final` 모드는 기존 Worker 3대를 HDFS 제외 목록(`dfs.hosts.exclude`)에 함께 추가한다. `hdfs dfsadmin -refreshNodes` 뒤 세 DataNode가 모두 `Decommissioned`가 될 때까지 세 서비스를 계속 기동한다. 한 노드라도 진행 중이면 기존 Worker 서비스를 중지하지 않는다. [Hadoop 3.5 HDFS 관리](https://hadoop.apache.org/docs/r3.5.0/hadoop-project-dist/hadoop-hdfs/HdfsUserGuide.html#DFSAdmin_Command)
2. `final`은 YARN 제외 목록에도 기존 Worker 3대를 함께 기록한다. `yarn rmadmin -refreshNodes -g 3600 -server`처럼 graceful decommission을 요청하고, 세 노드의 실행 중 작업이 끝났는지 확인한다. 제한 시간 도달은 작업 성공 증명이 아니다. [Hadoop 3.5 YARN decommission](https://hadoop.apache.org/docs/r3.5.0/hadoop-yarn/hadoop-yarn-site/GracefulDecommission.html)
3. HDFS에서 기존 세 노드가 모두 `Decommissioned`이고 YARN 실행 작업이 없으며 missing/corrupt/under-replicated block이 0인 것을 확인한 뒤 기존 서비스를 중지한다. 이때 원하는 복제본 2개가 모두 새 계정 Worker에 존재해야 한다. 한 대씩 처리하려면 단계별 exclude 목록을 명시적으로 만들고 각 노드 완료 후 다음 목록을 적용하는 별도 절차가 필요하다.

### 7.5 종료 전 증거와 정리

다음 결과를 모두 기록한 뒤 기존 NodeManager/DataNode 서비스를 중지하고 EC2 종료를 진행한다.

- 구계정 DataNode 3대가 모두 `Decommissioned`, 구계정 NodeManager에 실행 중 컨테이너 없음.
- `hdfs fsck`에서 missing/corrupt block 0, 필요한 파일의 복제 2가 새 Worker에서 충족됨.
- 기존 파일 수·크기와 대표 파일 다운로드 체크섬 일치.
- 구계정 daemon을 중지한 상태에서 새 Worker 3대로 HDFS 읽기·쓰기와 YARN Spark 검증 성공.
- Master의 NameNode·ResourceManager 주소와 저장 경로 유지, 웹 서비스의 검증된 PostgreSQL snapshot 조회 정상.

이후 구계정의 대상 EC2 3대만 종료한다. 현재 데이터 볼륨은 `DeleteOnTermination=false`이므로 필요한 보존·백업을 확인한 뒤 해당 200 GiB EBS 3개를 별도로 삭제해야 비용이 멈춘다. 사용하지 않는 snapshot·공인 IP 할당·보안 그룹·키와 Tailnet 장치도 소유 계정·COSMOS 태그를 확인해 정리한다. 마지막으로 구계정의 COSMOS EC2/EBS 잔여 리소스와 청구 내역을 조회한다.

### 7.6 이전 비용

새·구 3대를 동시에 하루 운영하면 동일 사양 기준 계정 합산 기본 비용이 약 **$21.81/일**이 된다. 이전 완료 후에도 구계정 EBS를 남기면 그 비용은 계속된다.

초기 SSAFY→AWS 원본 적재는 AWS inbound 방향이다. 계정 이전 시에는 AWS→AWS 복제이며 실제 VPC·AZ·전송 주소와 relay 경로에 따라 지역 간/인터넷 전송 요금이 달라질 수 있다. Tailscale `direct` 표시만으로 무료 사설 전송이라고 단정하지 말고 실제 peer endpoint를 확인한다.

서울 인터넷 outbound의 첫 10 TB 구간은 계정 합산 월 100 GB 무료분을 넘으면 **$0.126/GB**, EBS snapshot 저장은 **$0.05/GB·월**이다. 원본 기준선은 102.06 GiB이고 복제 계수 2의 블록 payload는 약 204.12 GiB이며, 균형 조정·재시도·분석 결과를 포함하면 이동량이 더 커질 수 있다. 현재 잔액 $25~30을 전부 정상 운영에 쓰지 않고 이전·검증·실패 재시도 비용으로 남긴다. 전송 과금은 최종 실제 경로와 사용량으로 다시 확인한다.

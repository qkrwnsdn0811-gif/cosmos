# Hadoop configuration contract

`configure-cluster.py`는 Python 3 표준 라이브러리만 사용한다. `--output DIR`는 검토용 파일을 생성하고, `--apply`는 **실행한 호스트 한 대**에서 설정을 백업·설치한다. SSH, 패키지 설치, UFW, 서비스 제어, NameNode format, HDFS 데이터 변경을 실행하지 않는다.

## Inventory

```json
{
  "cluster_name": "cosmos",
  "user": "ubuntu",
  "java17_home": "/usr/lib/jvm/java-17-openjdk-amd64",
  "java21_home": "/usr/lib/jvm/java-21-openjdk-amd64",
  "master": {"name": "cosmos-master", "tailscale_ip": "100.70.0.1"},
  "workers": [
    {"name": "cosmos-worker-1", "tailscale_ip": "100.70.0.2"},
    {"name": "cosmos-worker-2", "tailscale_ip": "100.70.0.3"},
    {"name": "cosmos-worker-3", "tailscale_ip": "100.70.0.4"}
  ]
}
```

위 주소는 **예시**다. 실제 `tailscale ip -4` 결과를 넣는다. 비밀값·인증키는 inventory에 넣지 않는다. 기존 hostname을 바꾸지 않아도 Hadoop은 숫자 Tailscale IP를 광고한다. `/etc/hosts`에는 이름 매핑도 보존하고 관리 블록으로 추가한다. Tailscale DNS 수락 여부에 의존하지 않는다.

```sh
python3 configure-cluster.py --inventory inventory.json --node cosmos-master --mode migration --output /tmp/cosmos-master-rendered
sudo python3 configure-cluster.py --inventory inventory.json --node cosmos-master --mode migration --apply
```

`--apply` 사전 조건은 Linux root, 해당 `tailscale0`에 inventory IP가 실제 할당됨, Hadoop/Spark 및 Java 두 버전 설치다. Master는 `/data/hadoop/namenode/current/VERSION`이 이미 있어야 하며 Worker는 `/data`가 EBS 파일시스템으로 마운트되어 있어야 한다. 기존 XML의 NameNode/DataNode 저장 위치가 다른 경우 자동 변경을 거부한다.

원본 설정·기존 unit·hosts는 `/var/backups/cosmos-hadoop/<시각>-<노드>-<모드>/`에 먼저 백업한다. 기존의 관리 대상 외 XML/Spark 설정은 유지한다. 설치 중 오류가 발생하면 변경한 설정을 원본으로 돌린다. NameNode VERSION의 SHA-256도 변경 전후 확인한다. 이는 **설정 백업**이며, 전체 fsimage/edits 메타데이터의 오프라인 백업은 운영자가 서비스 전환 전에 별도로 수행한다.

## Storage and transition

- NameNode: `/data/hadoop/namenode`; DataNode: `/data/hadoop/datanode`. 기존 데이터를 이동하거나 삭제하지 않는다.
- `migration`: Master DataNode + Worker 3대, 총 4대. 기본 신규 파일 replication은 2다. **기존 replication 1 파일은 자동 변경되지 않는다.**
- `account-transition`: 새 `workers` 3대와 `retiring_workers` 3대를 동시에 허용하고 이미 decommission한 Master DataNode는 제외한다. 구·신 계정 6대에서 복제와 균형 조정을 마칠 때 사용한다.
- `final`: 허용 목록에는 Master를 유지하고 제외 목록에 Master를 넣어 정상 decommission 대상으로 만든다. 운영자가 `hdfs dfsadmin -refreshNodes` 후 decommission 완료·복제 상태·블록 건전성을 확인하고 Master DataNode를 중지해야 최종 3대가 된다.
- `retiring_workers`가 있는 `final`은 Master와 기존 Worker 3대를 HDFS exclude에, 기존 Worker 3대를 YARN exclude에 기록한다. 서비스 중지는 각 노드가 decommission 완료된 뒤 수행한다.
- unit 파일은 `/etc/systemd/system/cosmos-hadoop-{namenode,secondarynamenode,datanode,resourcemanager,nodemanager}.service` 중 각 역할에 맞게 생성한다. `manifest.json` 또는 `/etc/cosmos/hadoop/configuration-manifest.json`의 `desired_services`를 참조한다. 기존 Master DataNode unit과 데이터는 final 모드에서도 삭제하지 않는다.
- 실행 전에 HDFS `/user/ubuntu`, `/tmp/logs`, `/spark-history`를 적절한 소유자·권한으로 준비한다. 생성기는 HDFS를 수정하지 않는다.

## Ports and resources

| 서비스 | TCP 포트 |
| --- | --- |
| NameNode RPC / HTTP, SecondaryNameNode HTTP | 9000 / 19870 / 19868 |
| DataNode transfer / IPC / HTTP | 19866 / 19867 / 19864 |
| ResourceManager scheduler / tracker / client / admin / HTTP | 18030 / 18031 / 18032 / 18033 / 18088 |
| NodeManager localizer / container / HTTP | 18040 / 18041 / 18042 |
| Spark driver / block manager / UI | 18100 / 18101 / 18104 |

모든 포트는 필요한 Tailscale peer만 접근하도록 UFW·Tailscale 정책에서 제한한다. NameNode RPC만 `0.0.0.0:9000`으로 bind하여 기존 Master의 `hdfs://localhost:9000` 클라이언트를 유지한다. 나머지 Hadoop 서비스는 해당 Tailscale IP에 bind한다. `spark.yarn.am.port`는 폐기된 옵션이므로 사용하지 않는다.

Worker는 2 vCPU / 8GiB 기준 YARN memory 5120MiB, vcores 2를 할당한다. 운영 Spark는 executor 3개, 각각 1 core / heap 2GiB / overhead 1GiB다. 컨테이너 하나가 NM 메모리의 절반보다 커서 한 Worker에 두 executor가 모이지 않으며 고정 block-manager 포트와 충돌하지 않는다. 고정 포트를 사용하는 Spark 애플리케이션은 한 번에 하나씩 실행한다. `/data` 볼륨 용량은 설치 단계가 관리한다.

Hadoop daemon은 Java 17이다. Spark submit/driver는 `spark-env.sh`의 Java 21, YARN AM 및 executor는 각각 `spark.yarn.appMasterEnv.JAVA_HOME`과 `spark.executorEnv.JAVA_HOME`의 같은 Java 21을 사용한다. Hadoop과 Spark에 서로 다른 JDK를 사용하는 방식은 [Spark 4.2 공식 YARN 문서](https://spark.apache.org/docs/4.2.0/running-on-yarn.html#configuring-different-jdks-for-spark-applications)의 구성 방법을 따른다. Hadoop 3.5의 Java 17 지원도 [같은 공식 문서](https://spark.apache.org/docs/4.2.0/running-on-yarn.html#launching-spark-on-yarn)에 명시되어 있다.

```sh
python3 -m unittest discover -s deploy/hadoop/tests -p 'test_configure_cluster.py' -v
```

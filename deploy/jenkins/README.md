# COSMOS Jenkins controller

공식 Jenkins `2.568.3-jdk21` 이미지에 필요한 플러그인과 초기 설정만 추가한다. 컨트롤러 실행기는 0개이며 Docker 소켓을 마운트하지 않는다. 빌드는 호스트의 `cosmos-agent`에서 `cosmos-build` 라벨로 실행한다.

## 서버 입력

아래 필수 파일 3개를 EC2의 `/etc/cosmos/jenkins/`에 준비한다. Git 저장소에 넣지 않는다. 컨테이너의 Jenkins UID `1000`이 디렉터리를 탐색하고 파일을 읽을 수 있어야 한다. 예를 들어 디렉터리는 `root:1000` / `0750`, 파일은 `root:1000` / `0640`으로 제한한다.

| 파일 | 용도 |
| --- | --- |
| `admin-user` | Jenkins 관리자 ID, 3~64자의 영문·숫자·점·밑줄·하이픈 |
| `admin-password` | 8자 이상의 관리자 비밀번호. 최초 설치 시에는 무작위 64자 값을 생성한다. |
| `webhook-token` | 32자 이상의 GitLab webhook 공유 비밀값 |

비밀값은 환경변수, 이미지, 명령행 인수에 넣지 않는다. 초기 설정은 파일을 읽어 관리자 계정을 구성한다. 재시작 시 관리자 계정은 이 파일들의 값으로 다시 적용하므로 비밀번호 변경도 파일과 함께 관리한다. 필수 비밀 파일이 없거나 읽을 수 없으면 HTTP 서버를 시작하지 않는다. 초기 Groovy 설정이 실패하면 Docker healthcheck는 실패하고, 최초 부팅의 기본 설정도 익명 접근을 허용하지 않는다.

Git 인증정보는 사용자가 프로젝트 Deploy Token을 Jenkins UI에 직접 등록한다. 토큰을 등록하지 않아도 로그인 가능한 컨트롤러와 agent 설정까지 완료되고 healthcheck는 정상 상태가 될 수 있다. Job은 `cosmos-git-read` ID를 참조하며, 실제 저장소 checkout은 이 credential 등록 후 가능하다. 빈 credential이나 임시 비밀번호는 생성하지 않는다.

## 프로젝트 Deploy Token 직접 등록

1. GitLab의 **이 프로젝트**에서 `Settings → Repository → Deploy tokens`를 연다. 프로젝트 저장소 읽기 용도로 토큰을 생성하고 `read_repository` 권한만 선택한다. 만료일도 지정한다.
2. 생성 결과의 **Username**과 **Token**을 확인한다. Username은 GitLab이 표시한 deploy token 사용자명이며, 개인 로그인 ID나 토큰 이름으로 바꾸지 않는다.
3. Jenkins에 관리자 로그인한 뒤 `Manage Jenkins → Credentials → System → Global credentials (unrestricted) → Add Credentials`를 연다.
4. **Kind**는 `Username with password`, **Scope**는 `Global`을 선택한다. **Username**에 Deploy Token Username, **Password**에 Token을 입력한다. **ID**는 정확히 `cosmos-git-read`로 지정하고 저장한다. Description은 `COSMOS project Deploy Token — read_repository`처럼 구분 가능한 문구로 입력한다.
5. 이미 `cosmos-git-read`가 있으면 새 ID를 만들지 말고 해당 credential을 수정한다. 등록 후 `cosmos-ci-116`을 수동 실행하여 checkout을 검증한다. `cosmos-web`은 `dev`에 `Jenkinsfile`이 merge된 뒤 사용한다.

로컬 PC에 저장된 Git 인증정보를 복사하지 않는다. Deploy Token 값은 채팅, Git 저장소, Jenkinsfile에 넣지 않는다. 두 Git 비밀 파일이 없는 기본 운영 방식에서는 UI에서 만든 `cosmos-git-read`를 재시작해도 그대로 유지한다.

별도의 자동 프로비저닝이 필요할 때만 `/etc/cosmos/jenkins/git-username`과 `git-password`를 **둘 다** 제공할 수 있다. 이 경우 파일 값으로 `cosmos-git-read`를 생성하거나 갱신한다. 둘 중 하나만 있거나 비어 있으면 시작을 거부한다. 현재 UI 직접 등록 방식에서는 두 파일을 만들지 않는다.

## 실행과 연결

서버에서 이 디렉터리를 기준으로 실행한다.

```sh
docker compose up -d --build
docker compose ps
```

컨트롤러는 `127.0.0.1:18090`에만 바인딩한다. Nginx는 `/jenkins/` 경로를 동일한 prefix로 전달하고 WebSocket upgrade를 지원해야 한다. 외부 URL은 `https://j15c205.p.ssafy.io/jenkins/`이다. `50000` 포트는 열지 않는다. Jenkins CSRF 보호와 로그인은 활성화되어 있다.

초기화가 끝나면 agent 연결 비밀값을 컨트롤러 볼륨 `/var/jenkins_home/cosmos-agent.secret`에 `0600` 권한으로 생성한다. 서버 bootstrap에서 명령 출력을 화면에 표시하지 않고 agent 전용 보호 파일로 직접 저장한다. 호스트 systemd 서비스는 Java 21과 컨트롤러의 `/jenkins/jnlpJars/agent.jar`를 사용한다.

```sh
java -jar /opt/cosmos/jenkins-agent/agent.jar \
  -url http://127.0.0.1:18090/jenkins/ \
  -name cosmos-agent \
  -secret @/etc/cosmos/agent.secret \
  -webSocket \
  -workDir /opt/cosmos/jenkins-agent
```

`agent.secret`은 agent 사용자만 읽을 수 있도록 bootstrap에서 설정한다. Docker 실행 권한을 가진 host agent는 서버에 강한 권한을 갖기 때문에 팀에서 신뢰하는 저장소/브랜치만 이 agent에서 실행한다.

## Pipeline과 트리거

| Job | SCM 브랜치 | 자동 트리거 |
| --- | --- | --- |
| `cosmos-web` | `dev` | `dev` push webhook |
| `cosmos-ci-116` | `chore/S15P21C205-116-jenkins-ec2-cicd` | 없음, 기능 브랜치 수동 검증용 |

두 job은 저장소 루트의 `Jenkinsfile`을 읽는다. 초기 설정 자체가 빌드를 예약하지 않는다. Job은 처음에만 생성하며 이후 재시작에서는 기존 Job 설정을 보존한다. 따라서 UI에서 변경한 SCM, 트리거, 비활성화 상태를 초기화 코드가 덮어쓰지 않는다. `dev`에 `Jenkinsfile`이 merge된 이후 운영 job을 사용한다. 기능 브랜치 job의 실제 배포 여부는 `Jenkinsfile`의 job/브랜치 검증 조건이 결정한다.

GitLab 프로젝트 webhook에 아래 값을 등록한다.

- URL: `https://j15c205.p.ssafy.io/jenkins/project/cosmos-web`
- 이벤트: Push events
- 브랜치 필터: `dev`
- Secret token: 서버 `webhook-token` 파일과 동일한 값
- SSL verification: 활성화

Jenkins에서도 `dev`만 허용하며 MR, 댓글, branch 삭제 이벤트는 비활성화한다. GitLab API write token은 이 구성에 필요하지 않다. 따라서 GitLab에 commit status를 자동 게시하지는 않는다.

기존 Job은 재시작 시 보존되므로 webhook token을 교체할 때는 `cosmos-web → Configure → Build Triggers → Build when a change is pushed to GitLab → Advanced → Secret token`과 GitLab webhook 양쪽도 함께 갱신한다.

GitLab webhook 등록 권한이 없을 때는 credential을 등록하고 최초 수동 빌드로 SCM 기준점을 만든 뒤, `cosmos-web → Configure → Build Triggers → Poll SCM`에서 `H/5 * * * *`를 설정하여 5분 간격 polling을 활성화할 수 있다. `COSMOS_ENABLE_POLLING`의 기본값은 `false`이며 이 환경변수는 Job 최초 생성에만 적용된다. Polling은 이후 `dev` 변경에 빌드를 예약하므로 운영 준비가 끝난 뒤 켠다.

플러그인 직접 의존 버전은 `plugins.txt`에 고정했다. 전이 의존 버전은 공식 `jenkins-plugin-cli`가 이미지 빌드 시 해결한다. 이미지 갱신은 controller backup 후 별도로 검증한다.

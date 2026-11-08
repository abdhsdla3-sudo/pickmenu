# Windows LLM PC 접속

개인 PC에서 모델 서버를 `127.0.0.1:8080`에 바인딩하고, 외부 개발 PC나 클라우드 VM은 사설망을 거쳐 SSH로 해당 포트에 연결한다. 아래는 Tailscale 사설망과 Windows OpenSSH를 사용하는 구성이다.

```text
개발 PC 또는 클라우드 VM의 127.0.0.1:18080
  → 사설망의 Windows OpenSSH 22
  → Windows PC의 127.0.0.1:8080 (LLM)
```

1. Windows PC와 접속할 개발 PC 또는 VM을 같은 Tailscale 사설망에 등록한다. 정책에서 승인한 개발 기기나 VM만 Windows LLM 호스트의 `tcp:22`에 연결하도록 허용한다. 기존의 넓은 허용 규칙과 겹치는지도 확인한다. [Tailscale 권한 규칙](https://tailscale.com/docs/reference/syntax/grants)
2. Windows PC에는 Windows OpenSSH Server를 설치한다. 별도 일반 사용자 계정과 SSH 공개 키를 사용하고, Windows 방화벽의 OpenSSH 수신 규칙을 Tailscale 인터페이스와 승인된 주소로 제한한다. 공유기 포트 포워딩과 클라우드 인터넷 인바운드 없이 운영한다. 관리 권한이 필요한 설치와 방화벽 변경은 PC에서 직접 수행한다. [Windows OpenSSH 설정](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration) · [SSH를 사설망으로 보호하기](https://tailscale.com/docs/reference/ssh-over-tailscale)
3. 모델 서버를 실행한 뒤 Windows PC에서 모델 서버 자체가 응답하는지 확인한다. 아래 명령은 OpenAI 호환 API를 제공하는 서버 기준이다. 다른 런타임을 사용하면 해당 런타임의 상태 확인 경로로 바꾼다.

```bat
curl.exe --fail --silent --show-error http://127.0.0.1:8080/v1/models
```

모델 호출에만 쓰는 Windows SSH 계정은 `%ProgramData%\ssh\sshd_config`에서 허용 포워딩을 좁힐 수 있다. 아래 블록의 계정명을 실제 계정으로 바꾸고, 기존 설정에 적용하기 전에 `sshd.exe -t`로 검증한다. Windows OpenSSH 서비스를 다시 시작하면 변경이 반영된다. [Windows OpenSSH 설정](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration)

```text
Match User llmforward
    AuthenticationMethods publickey
    PasswordAuthentication no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:8080
    MaxSessions 0
```

접속하는 PC에서 `%USERPROFILE%\.ssh\config`에 아래와 같이 추가한다. Linux에서는 `~/.ssh/config`를 사용한다. 호스트 이름은 Windows PC의 사설망 DNS 이름으로, 키 경로와 계정은 발급한 값으로 바꾼다.

```text
Host pickmenu-llm
    HostName YOUR_PC.YOUR_TAILNET.ts.net
    User llmforward
    IdentityFile ~/.ssh/pickmenu_llm_ed25519
    IdentitiesOnly yes
```

외부 개발 PC 또는 VM의 터미널에서 연결한다. 첫 접속 시에는 Windows PC에서 확인한 SSH 호스트 키 지문과 비교한다.

```bat
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18080:127.0.0.1:8080 pickmenu-llm
```

SSH 창을 열어 두고 다른 CMD에서 확인한다. Linux에서는 `curl.exe` 대신 `curl`을 사용한다. 모델 서버 자체가 API 키를 요구하면 요청에 그 서버가 발급한 키를 추가한다.

```bat
curl.exe --fail --silent --show-error http://127.0.0.1:18080/v1/models
```

매장 추천 서버의 LLM 클라이언트는 개발용 기준 주소 `http://127.0.0.1:18080/v1`을 사용할 수 있다. 백엔드를 컨테이너에서 실행하는 경우 SSH 포워딩을 같은 네트워크 네임스페이스에서 실행하거나 사설 네트워크 경로를 별도로 구성한다. Windows PC가 절전·종료 상태이거나 모델 서버가 내려가면 연결이 실패하므로, 매장 서비스는 이를 별도 오류로 처리한다.

Windows PC의 파일을 Codex에서 직접 편집하려면 모델 포워딩 전용 계정과 구분해 ChatGPT 데스크톱의 원격 연결을 사용한다. [Codex 접속 안내](codex-access.md)

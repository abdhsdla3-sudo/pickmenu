# 다른 기기에서 픽메뉴 개발하기

## 다른 PC에서 코드를 이어서 수정

GitHub에서 비공개 픽메뉴 저장소를 읽을 수 있는 계정으로 로그인한다. 새 PC에서 Windows CMD를 열고 실행한다.

```bat
gh auth login
gh auth setup-git --hostname github.com
gh repo clone abdhsdla3-sudo/pickmenu pickmenu-dev
cd /d pickmenu-dev
git status
git tag --list
```

ChatGPT 데스크톱에서 Codex를 열고 복제한 `pickmenu-dev` 폴더를 로컬 프로젝트로 추가한다. 이 PC에서 기존 캡스톤 프로젝트를 계속 쓰려면 프로젝트 편집에서 `pickmenu-dev`를 추가하고 주 폴더로 선택한다. 그러면 새 채팅은 저장소의 `AGENTS.md`와 Git 설정을 읽는다. 각 환경에서 같은 저장소의 루트 폴더를 선택하면 파일 구조가 일치한다. [OpenAI 공식 프로젝트 문서](https://learn.chatgpt.com/docs/projects)

코드는 GitHub에서 받는다. 데이터·모델·비밀값은 접근 권한이 부여된 별도 위치에서 준비한다. 현재 PC에 있는 카탈로그로 실행하는 방법과 데이터 없이 돌릴 수 있는 테스트는 [저장소 README](../README.md)에 있다.

## 이 PC에서 진행 중인 기존 채팅 이어가기

기존 캡스톤 채팅과 이 PC에 있는 파일이 필요한 경우에는 이 PC를 켜 두고 ChatGPT 데스크톱의 **Settings → Connections**에서 **Allow connections** 또는 이 기기에 대한 원격 제어를 설정한다. 다른 기기에서는 같은 ChatGPT 계정과 워크스페이스로 로그인하고 **Control other devices**에서 이 PC를 페어링한다. 해당 메뉴와 사용 가능 기기는 앱 버전과 워크스페이스 설정에 따라 달라질 수 있다. 원격으로 실행한 명령은 연결된 호스트에서 실행된다. [OpenAI 공식 원격 연결 문서](https://learn.chatgpt.com/docs/remote-connections)

클라우드 VM에서 코드를 직접 수정하려면, 해당 VM에 같은 저장소를 복제하고 SSH 키와 Codex CLI 인증을 준비한다. 개발 PC의 SSH 설정에 VM 호스트를 추가하고 `ssh` 연결을 확인한 후 데스크톱 Codex의 **Settings → Connections → SSH**에서 VM과 복제한 폴더를 선택한다. 기존 채팅을 옮기려면 양쪽 호스트에 같은 Git 저장소의 루트가 프로젝트로 저장돼 있어야 한다. 그다음 채팅 하단의 실행 위치에서 원격 호스트로 넘긴다. [OpenAI 공식 원격 연결 문서](https://learn.chatgpt.com/docs/remote-connections#hand-off-a-chat-between-hosts)

## Codex Cloud에서 작업

Codex의 **Work in → Cloud → Select environment → Create environment**에서 비공개 저장소를 선택하고 환경을 설정한다. 코드와 테스트를 먼저 검증하고, 필요한 경우 워크스페이스 권한으로 환경을 게시한다. Cloud 작업에서 Windows PC의 실행 중인 LLM이 필요하면 아래 [LLM 접속 가이드](llm-access.md)의 통제된 네트워크를 사용할 수 있는 VM 또는 원격 호스트에서 실행한다. Codex Cloud의 기본 환경에는 로컬 PC의 VPN이나 파일이 자동으로 따라오지 않는다. [OpenAI 공식 Cloud 환경 문서](https://learn.chatgpt.com/docs/environments/cloud-environments)

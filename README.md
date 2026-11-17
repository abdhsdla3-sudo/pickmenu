# 픽메뉴

픽메뉴의 소스와 개발 가이드를 관리하는 비공개 저장소다. 공개 데모는 Sites에서 볼 수 있다. [픽메뉴 데모](https://pickmenu-suwon-demo.jmk031204.chatgpt.site/)

| 위치 | 역할 |
| --- | --- |
| `pickmenu/web/` | 화면 원본. Codex로 수정하고 Sites 배포본에서 재사용 |
| `pickmenu/server.py`, `pickmenu/recommend.py` | 메뉴 API와 추천 로직 |
| `pickmenu/auth.py` | 회원가입·로그인 세션과 계정 프로필 저장 |
| `pickmenu/prepare.py` | 리뷰와 영양 데이터 전처리 |
| `pickmenu-share/` | Sites Worker, 메뉴 API 연결, 공개용 카탈로그 내보내기 |
| `docs/` | 클라우드 이전, 기기 간 개발, LLM 연결, 버전 관리 |

- [서비스 구조와 클라우드 이전](docs/architecture.md)
- [다른 기기에서 Codex로 개발하기](docs/codex-access.md)
- [Windows LLM PC에 접속하기](docs/llm-access.md)
- [버전과 릴리스 관리](docs/releases.md)
- [모바일 앱형 데모 흐름](docs/mobile-demo.md)
- [회원 계정과 로그인](docs/accounts.md)
- [2026-11-14 자료 수정 반영과 재실행](docs/data-update-20261114.md)
- [데이터와 추천 기준](pickmenu/DATA.md)

Windows CMD에서 GitHub에 인증한 뒤 저장소를 내려받는다.

```bat
gh auth login
gh auth setup-git --hostname github.com
gh repo clone abdhsdla3-sudo/pickmenu pickmenu-dev
cd /d pickmenu-dev
python -m pip install -r pickmenu\requirements-preprocess.txt ruff mypy
cd pickmenu
python -X utf8 -m unittest discover -s tests -v
```

추천까지 실행하려면 접근 권한이 있는 경로에서 생성한 `catalog.sqlite3`를 받아 `PICKMENU_CATALOG_PATH`에 지정한다. 이 PC의 기존 카탈로그를 재사용하는 CMD 명령은 아래와 같다.

```bat
cd /d "D:\학교\3학년\캡스톤\pickmenu-dev\pickmenu"
set "PICKMENU_CATALOG_PATH=D:\학교\3학년\캡스톤\pickmenu\local\catalog-v03.sqlite3"
start.cmd --port 8766
```

브라우저에서 [새 체크아웃 테스트](http://127.0.0.1:8766/)를 연다. 일반 실행은 기본 포트 8765를 사용한다. 카탈로그 없이 실행하면 화면은 열리며 추천 API는 데이터가 필요하다는 응답을 반환한다. 원본부터 다시 생성하려면 [전처리 안내](pickmenu/README.md)를 따른다.

기존 카탈로그로 전체 데이터 테스트를 할 때는 같은 전처리에서 생성한 감사 JSON 폴더를 `PICKMENU_AUDIT_DIR`로 지정한다. 공유본 비교 검사에 다른 포트를 쓴다면 `PICKMENU_VERIFY_ORIGIN`에 로컬 서버 주소를 지정한다.

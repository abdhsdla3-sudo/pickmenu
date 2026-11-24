# 픽메뉴

픽메뉴의 소스와 개발 가이드를 관리하는 비공개 저장소다.

- 서비스 주소: https://pickmenu-dev.tail048532.ts.net (휴대폰·노트북에서 바로 접속, HTTPS)
- 관리자 화면: https://pickmenu-dev.tail048532.ts.net/admin (관리자 권한 계정만, [관리자 페이지](docs/admin.md))
- 구성: VMware Ubuntu VM의 Python API·SQLite 카탈로그·PostgreSQL 참고자료 + Windows GPU의 Ollama(파인튜닝 Qwen3-8B). 공개 HTTPS는 Tailscale Funnel이 앞단을 맡는다.

| 위치 | 역할 |
| --- | --- |
| `pickmenu/web/` | 화면 원본. Codex로 수정하고 Sites 배포본에서 재사용 |
| `pickmenu/server.py`, `pickmenu/recommend.py` | 메뉴 API와 추천 로직 |
| `pickmenu/auth.py` | 회원가입·로그인 세션과 계정 프로필 저장 |
| `pickmenu/menu_reco.py` | 추천 기준표 v1.4 음식·가게 추천(`/api/v2`, PostgreSQL 참고자료) |
| `pickmenu/prepare.py` | 리뷰와 영양 데이터 전처리 |
| `pickmenu-share/` | Sites Worker, 메뉴 API 연결, 공개용 카탈로그 내보내기 |
| `docs/` | 클라우드 이전, 기기 간 개발, LLM 연결, 버전 관리 |

- [서비스 구조와 클라우드 이전](docs/architecture.md)
- [다른 기기에서 Codex로 개발하기](docs/codex-access.md)
- [Windows LLM PC에 접속하기](docs/llm-access.md)
- [버전과 릴리스 관리](docs/releases.md)
- [관리자(Admin) 페이지](docs/admin.md)
- [모바일 앱형 데모 흐름](docs/mobile-demo.md)
- [회원 계정과 로그인](docs/accounts.md)
- [가입 기본정보·암호화·마스킹](docs/account-privacy.md)
- [가게까지 추천 (추천 기준표 v1.4)](docs/menu-recommend.md)
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

추천까지 실행하려면 접근 권한이 있는 경로에서 생성한 `catalog.sqlite3`를 받아 `PICKMENU_CATALOG_PATH`에 지정한다. 저장소 루트에서 아래 CMD 명령을 실행한다. 카탈로그가 다른 폴더에 있다면 해당 경로를 지정한다.

```bat
cd pickmenu
set "PICKMENU_CATALOG_PATH=%CD%\local\catalog-v03.sqlite3"
start.cmd --port 8766
```

브라우저에서 [새 체크아웃 테스트](http://127.0.0.1:8766/)를 연다. 일반 실행은 기본 포트 8765를 사용한다. 카탈로그 없이 실행하면 화면은 열리며 추천 API는 데이터가 필요하다는 응답을 반환한다. 원본부터 다시 생성하려면 [전처리 안내](pickmenu/README.md)를 따른다.

기존 카탈로그로 전체 데이터 테스트를 할 때는 같은 전처리에서 생성한 감사 JSON 폴더를 `PICKMENU_AUDIT_DIR`로 지정한다. 공유본 비교 검사에 다른 포트를 쓴다면 `PICKMENU_VERIFY_ORIGIN`에 로컬 서버 주소를 지정한다.

## 배포

서버(VM)에서는 개발 폴더를 덮어쓰지 않고 버전별 릴리스 폴더로 바꾼다. 자세한 절차는 [버전과 릴리스 관리](docs/releases.md)에 있다.

```sh
# 새 릴리스 폴더 ~/pickmenu-releases/vX.Y.Z (source/ = 커밋을 git archive로 푼 코드, catalog.sqlite3, systemd-release.conf, deploy.sh)
~/pickmenu-releases/vX.Y.Z/deploy.sh --check     # 코드 버전·카탈로그·커밋 점검, 서비스는 그대로
~/pickmenu-releases/vX.Y.Z/deploy.sh --apply     # 서비스를 새 폴더로 바꾸고 화면·API 응답 확인
~/pickmenu-releases/vX.Y.Z/deploy.sh --rollback  # 이전 릴리스 설정으로 되돌림
```

## 비밀 설정

API 키(네이버 지도), DB 접속 정보, 회원 계정 암호화 키 경로는 서버의 systemd 환경변수와 권한 600 파일에만 둔다. 화면(JavaScript)과 저장소에는 넣지 않는다. 브라우저는 같은 출처의 픽메뉴 API만 부르고, 지도 검색·AI 호출은 서버가 대신한다. 원본 리뷰, 개인 식별자, DB, 모델 가중치, 생성된 카탈로그도 커밋하지 않는다(AGENTS.md).

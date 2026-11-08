# 픽메뉴 로컬 데모

수원시 배달 리뷰에 기록된 식사 메뉴를 영양 목표와 취향에 맞춰 최대 5개 보여준다. 프로필 저장과 이번 식사 조건은 분리돼 있다. 선택한 메뉴의 `standard_menu_id`를 이후 매장 추천에 전달할 수 있다.

## Windows CMD 실행

Python 3.11 이상이 필요하다. 현재 작업 폴더에는 전처리 결과가 준비되어 있다.

```bat
chcp 65001 > nul
cd /d "D:\학교\3학년\캡스톤\pickmenu-dev\pickmenu"
start.cmd
```

브라우저에서 [픽메뉴 열기](http://127.0.0.1:8765/). 이미 서버가 떠 있으면 주소를 바로 열 수 있다. CMD에서 띄운 서버는 `Ctrl+C`로 종료한다. 별도 포트가 필요하면 `start.cmd --port 8766`을 실행한다. 이 작업에서 띄운 백그라운드 서버는 `local\server.pid`에 PID를 기록했다. 해당 PID가 픽메뉴 Python 서버임을 확인한 뒤 작업 관리자에서 종료할 수 있다.

## 데이터 준비와 갱신

작업 위치 상위 폴더에 제공 원본 리뷰 폴더와 같은 이름의 ZIP을 둔다. 공유 Drive 파일은 `local\drive`에 원래의 하위 폴더 구조로 둔다. Google Sheets 파일은 별도로 내려받은 `04_수원시_리뷰평가분류_2607-2609 (1).xlsx`로 구분한다. [공유 Drive](https://drive.google.com/drive/folders/1vktvcUQ-rfI4HBzP6ruMaXWywee2R_8U)

```bat
cd /d "D:\학교\3학년\캡스톤\pickmenu-dev\pickmenu"
python -m pip install -r requirements-preprocess.txt
python -X utf8 -u prepare.py
```

전처리는 CSV와 XLSX를 스트리밍으로 읽는다. SHA-256이 같은 완료 파일은 다시 읽는 대신 저장된 검사·전처리 결과를 재사용한다. 실패 후 같은 명령을 실행하면 마지막 미완료 파일부터 작업한다. 변경된 파일은 이전 행을 같은 트랜잭션에서 교체한다. 카탈로그는 임시 파일에서 만들고 검사한 뒤 교체한다. Windows에서 서버가 파일을 읽는 순간에 교체가 실패하면 서버를 종료하고 같은 명령을 다시 실행한다. 스키마가 바뀌면 먼저 `raw.py` 또는 `drive_data.py`의 계약과 매핑을 검토한다. 전처리 코드의 원본 해석·열 선별 규칙을 바꾼 경우 서버를 종료한 뒤 생성 캐시인 `local\raw.sqlite3` 또는 `local\drive.sqlite3`를 다른 이름으로 옮기고 다시 실행한다.

경로에 한글과 공백이 있으므로 CMD에서는 위와 같이 경로를 큰따옴표로 감싼다. 스크립트는 자기 위치를 기준으로 동작한다. 서버는 Python 표준 라이브러리와 `local\catalog.sqlite3`로 실행하며, XLSX를 읽는 전처리와 테스트에는 `openpyxl`이 필요하다.

## 추천 규칙

1. 수원시 전체 또는 4개 구에서 2026년 7~9월 KST 리뷰 근거가 있는 메뉴를 조회한다. 검색어는 표준 메뉴명·음식 분류·구성 음식의 공백을 제거한 뒤 포함 여부를 확인한다.
2. 채식·비건 및 기피 재료·알레르기는 먼저 제한한다. 이름·분류·구성에서 충돌 단서가 보이면 제외한다. 전체 원재료와 교차접촉 정보가 없어 나머지는 별도 확인 대상으로 분리한다. 제한을 만족한다고 검증된 메뉴가 없으면 추천은 0개다. 일반식과 육식 선호에도 실제 원재료 확인 안내를 표시한다.
3. 100g으로 대조 가능한 단품 참고값으로 영양 점수 `N`을 계산한다. `clip`은 값을 0~1로 제한한다. 단백질 중심은 `clip(단백질 g / 12)`, 가벼운 식사는 `clip(1 - (kcal - 80) / 220)`이다. 균형 식사는 단백질·탄수화물·지방을 4·4·9 kcal/g로 환산한 열량 비중과 데모 목표 22%·53%·25% 사이 절대차 합을 1에서 뺀 뒤 제한한다. 영양값 결측은 그대로 두고 해당 점수의 순위 기여분에만 기본값 0.30을 적용한다.
4. 취향 점수 `T`는 선택한 선호 항목의 평균이다. 일치 1, 불일치 0, 단서 부족 0.35를 사용한다. 선호 미입력 시 0.5다. 맵기·국물은 메뉴명·구성·음식군에서 추정한다. 강도를 판정할 단서가 없으면 미상으로 처리한다. 음식군, 맵기, 국물, 육식은 점수에만 반영하며 UI에 일치·불일치·정보 부족을 표시한다.
5. 지역 근거 `E`는 `0.7 × clip(log1p(고유 리뷰수) / log1p(150)) + 0.3 × clip(log1p(매장수) / log1p(10))`이다. 최종 점수는 `100 × (0.50N + 0.35T + 0.15E)`다. 소수점 셋째 자리까지 계산한 점수 내림차순, 고유 리뷰 수 내림차순, 표준 메뉴 ID 오름차순으로 정렬한다. 첫 번째 구성 음식이 같은 메뉴는 각 음식의 첫 후보를 먼저 보여주고, 자리가 남으면 중복 음식의 다른 후보로 채운다.

계수와 가중치는 데모 초기 가정이다. 이전 캡스톤의 「메뉴 추천 점수 방식 검토」에서 논의한 제한 조건 분리, 가중평균, 결측 처리와 결과 다양성을 반영했다. 「네이버 클라우드 서버 구성 제안」과 「추천 서비스 기획 정리」의 역할 분리를 따라 웹 화면·추천 로직·전처리를 모듈로 구분하고 `/api` 상대 경로와 표준 메뉴 ID를 사용했다.

## 인터페이스와 파일

| 위치 | 역할 |
| --- | --- |
| `web/` | 반응형 화면, 브라우저 프로필 저장·수정·초기화, 추천과 상세 |
| `server.py` | `GET /api/catalog`, `POST /api/recommend`, `GET /api/menus/{standard_menu_id}` |
| `recommend.py` | 입력 검증, 제한 정책, 점수와 근거 문장 |
| `raw.py`, `drive_data.py`, `prepare.py` | 원본 감사, Drive 전수 검사, 카탈로그 생성 |
| `local/catalog.sqlite3` | 추천용 `catalog`, 추적용 `trace`·`review` |
| `local/*-audit.json` | 파일·시트별 행 수, 제외·실패·결측, 키 대조 결과 |

화면에서는 표준 메뉴와 집계, 공개 영양정보만 제공한다. DB와 원본 파일은 정적 경로에서 제공하지 않는다. 프로필 저장 버튼은 식단·영양 목표·취향·제한 조건을 해당 브라우저에 보관한다. 검색어와 지역은 이번 요청에만 사용한다.

## 검사

```bat
cd /d "D:\학교\3학년\캡스톤\pickmenu-dev\pickmenu"
python -m pip install -r requirements-preprocess.txt ruff mypy
python -X utf8 -m unittest discover -s tests -v
python -m ruff check .
python -m mypy raw.py drive_data.py prepare.py recommend.py server.py tests
python -m compileall -q raw.py drive_data.py prepare.py recommend.py server.py tests
node --check web\app.js
```

Node는 정적 JavaScript 구문 검사에만 쓰인다. 브라우저가 정적 파일을 직접 읽는 구성이라 별도의 번들 빌드가 없다. [데이터 인벤토리와 검증 결과](DATA.md)

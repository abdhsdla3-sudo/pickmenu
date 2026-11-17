# 가게까지 추천 (추천 기준표 v1.3)

1.1.0부터 앱의 세 번째 추천 방식 **가게까지 추천받기**가 PostgreSQL `pickmenu_data`의 추천 v1.3 자료로 음식(컬럼) 5개 안팎과 음식별 가게 순위를 보여 준다. 기준은 DB `source_files`의 `추천기준표.md`와 웹 전달 README를 따른다. 기존 선택지형·대화형 추천(`/api/recommend`, SQLite 카탈로그)은 그대로다.

## 구성

| 위치 | 역할 |
| --- | --- |
| `pickmenu/menu_reco.py` | 조건 검사, 필터, 노출 등급, 컬럼 점수, 채우는 순서, 가게 순위, '수원 전체로 넓히기' 계산. DB 읽기와 5분 메모리 캐시 |
| `pickmenu/server.py` | `GET /api/v2/options`, `POST /api/v2/recommend` |
| `pickmenu/web/` | 추천 방식 화면의 `가게까지 추천받기`, 조건 폼, 음식 카드·가게 목록·넓히기 안내 |
| `pickmenu/tests/test_menu_reco.py` | 규칙 단위 테스트, API 테스트, DB 추천예시 대조 테스트 |

## 실행 설정

- 서버 가상환경에 `pip install -r pickmenu/requirements-runtime.txt`로 `psycopg`를 설치한다.
- `PICKMENU_REQUIRE_LOGIN=1`이면 `/api/v2` 경로도 로그인 세션을 요구한다.
- `PICKMENU_REFERENCE_DSN`에 접속 정보를 둔다. 없으면 `service=pickmenu`(서버 실행 계정의 `~/.pg_service.conf`와 `~/.pgpass`)를 쓴다. 읽기 권한만 있으면 된다.
- DB에 연결할 수 없으면 두 경로가 503을 반환하고, 화면의 다른 추천 방식은 계속 동작한다. 활성 배치는 5분마다 다시 읽는다.

## API

`POST /api/v2/recommend` 본문:

| 칸 | 값 |
| --- | --- |
| `district` | `""`(수원시 전체), `권선구`, `영통구`, `장안구`, `팔달구` |
| `goal` | `balance`, `protein` |
| `diet` | `general`, `meat`. `vegetarian`·`vegan`은 자료가 없어 400 |
| `groups` | 선호 음식군 목록(최대 5, 화면은 3). 비우면 골고루 |
| `spice` | `mild`이면 매움·약간 매움·보통 매움 메뉴 제외 |
| `broth` | `yes`는 국물 있음만, `no`는 국물 있음 제외 |

응답의 `items`는 음식 카드다. 칸: `column`, `grade`(`full`·`weak`), `store_count`, `review_count`, `nutrition_score`, `rating_level`, `column_score`, `notes`(약함·선호 밖·목표 점수 낮음), `stores`(가게당 대표 메뉴 1개, 최대 10곳). 가게에는 `rating_display`, `review_count`, `disappointing_reviews`, `store_repeat_ratio`를 주고 계산용 `rating`은 주지 않는다. `expand`, `expand_reason`, `expand_hints`, `unavailable`은 넓히기 안내다.

## 메뉴 상세의 가게 목록

선택지형·대화형 추천에서 `이 메뉴 선택`을 누르면 상세 창에 `이 메뉴를 파는 가게`를 보여 준다. `GET /api/v2/stores?food=<구성 음식>&district=<구>`가 기존 카탈로그 메뉴의 `components`와 `current_menu_scores.detail_food`(일치가 없으면 `upper_food`)가 **정확히 같은** 메뉴만 모아 가게 순위 규칙으로 정렬한다. 검증된 `standard_menu_id` 변환표가 없어 비슷한 이름은 연결하지 않는다. 2026-11-18 공개 데모 메뉴의 구성 음식 167개 중 151개가 일치했고, 부대찌개·순두부찌개 등 16개는 가게 리뷰 자료가 없다고 표시한다. 고른 구에 가게가 없으면 수원 전체 기준임을 밝혀 보여 준다.

## 규칙 요약

- 대상은 컬럼표의 식사이고 곁들임이 아닌 컬럼이다. 노출 등급은 필터 뒤 그 지역의 가게 수·리뷰 수로 정한다(충족 3곳·30건, 약함 3곳·30건 미만 또는 2곳·30건 이상).
- 컬럼 점수 = 0.5 영양(목표 점수 리뷰 가중 평균, 영양값 리뷰 비중 0.5 미만이면 후보 중앙값) + 0.3 인기(지역 리뷰 수 log 백분위, 기준은 식사 컬럼 중 가게 2곳 이상) + 0.2 별점수준(4.38→0, 4.83→1).
- 선호 컬럼은 컬럼표의 `선호음식군` 소속으로 정한다. 채우는 순서는 선호·충족 → 선호·약함 → 선호 밖·목표 적합 → 선호 밖·목표 부적합, 골고루면 목표 적합 → 부적합이고 첫 음식군이 겹치는 컬럼은 건너뛴다. 각 단계에서 '안 매운'이면 안 매움 메뉴가 없는 컬럼을 뒤로, 육식 선호면 육식 컬럼을 앞으로 둔 뒤 점수·리뷰 수 순이다.
- 가게 순위는 가게마다 리뷰가 가장 많은 메뉴를 대표로 고르고 추천점수 순이다. '안 매운'이면 안 매움 대표에 0.6을 더한다.
- 넓히기 안내와 '조건으로 없음'은 구를 골랐을 때 대응표의 ① 후보 컬럼만 대상으로 한다. 버튼 사유 우선순위는 추천 5개 미만 → 고른 음식 컬럼 1개 이하 → 약함 3개 이상이며, 수원 전체로 넓혀 실제로 더 나오는 것이 있을 때만 띄운다.

## 검증

`PICKMENU_REFERENCE_TEST=1`로 테스트를 실행하면 DB의 `current_recommendation_examples` 21개 프로필(P4 채식 제외)과 음식 순서·가게 수·리뷰 수·영양 점수·별점 수준·컬럼 점수·비고·넓히기 버튼과 사유·안내 목록·조건으로 없음·음식별 상위 3개 가게 문자열을 전부 대조한다. 2026-11-17 활성 배치 `9e0930e2…`에서 모두 일치했다.

## 한계

- 영양 점수와 별점 해석, 화면 문구의 제약은 `추천기준표.md` 5·6장을 따른다. 알레르기·채식 안전성은 이 자료로 판단하지 않는다.
- Sites 공유본(`pickmenu-share/worker.js`)은 아직 `/api/v2` 경로를 전달하지 않는다.
- 기존 `standard_menu_id` 카탈로그와 새 컬럼 사이의 변환표가 없어 두 추천 결과를 연결하지 않는다.

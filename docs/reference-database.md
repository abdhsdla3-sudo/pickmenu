# 추천·지도 참고자료 DB

0.5.0부터 PostgreSQL `pickmenu_dev`의 `pickmenu_data` 스키마에 다운로드한 추천 v1.3 자료와 지도 조사 자료를 보관한다. pgAdmin에서 `Databases → pickmenu_dev → Schemas → pickmenu_data → Tables`를 새로고침하면 조회할 수 있다. 추천 화면의 실행 카탈로그와 SQLite 회원 DB는 별개다.

## 자료별 역할

| 테이블 | 원본 | 키·용도 |
| --- | --- | --- |
| `menu_scores` | 메뉴_추천.csv | 통합매장ID+세부음식. 추천점수·별점·리뷰·맵기·국물 |
| `food_columns` | 컬럼표.csv | 컬럼. 식사·곁들임·① 후보·지역별 집계 |
| `food_nutrition` | 음식_영양.csv | 상위음식+세부음식. 100g당 영양 참고값·신뢰도·영양 비율 점수 |
| `preference_groups` | 선호음식군_대응표.csv | 음식군 선택지와 ① 후보 컬럼 대응 |
| `recommendation_examples` | 추천예시.csv | 프로필+순위. 개발 검증용, 실제 회원 데이터 아님 |
| `survey_stores` | 지도 엑셀 01_매장635 | 원본 매장ID. 주소·출처·게시시간·조사 상태 |
| `store_evidence` | 지도 엑셀 02_근거기록 | 원본 매장ID별 복수 근거·확인일·주의사항 |
| `maps_api_checks` | 지도 엑셀 04_Maps_API점검 | 매장ID별 API 상태·오류·신규 좌표 |
| `source_files`, `source_rows` | 8개 원본 전체 | 파일 해시·MD 본문·모든 원본 열·지도 설명 시트 |
| `import_batches`, `active_dataset` | 적재 이력 | 스냅샷별 보존과 현재 사용할 배치 지정 |

업무 열은 조회 가능한 SQL 타입으로 저장하고 전체 원문 열은 각 행의 `data` JSONB에도 보존한다. 예: `data->>'가게_권선구'`. 빈 문자열은 생성된 업무 열에서 NULL로 보이며 원문은 그대로다. CSV의 `1.0`은 정수값일 때만 integer로 변환한다. 조사 기준일처럼 일자·월·설명이 섞인 원문은 text로 보존한다.

각 테이블은 `batch_id`별로 보존한다. 일반 조회는 활성 배치만 반환하는 `current_` 뷰를 사용한다. 새 파일을 추가 적재해도 과거 배치를 삭제하지 않는다.

## 연결과 해석

- `menu_scores.column_name → food_columns.column_name`은 외래 키로 검증한다. 영양은 `upper_food + detail_food` 정확 일치로 LEFT JOIN한다. 양쪽 원본에서 세부음식이 비어 있고 상위음식이 같은 항목도 연결하며, 한쪽만 비어 있으면 연결하지 않는다. 결측을 0이나 임의 참고값으로 채우지 않는다. `current_menu_details`에서 확인한다.
- `current_preference_candidates`는 대응표에 명시된 ① 후보만 풀어 준다. 전체 컬럼의 음식군 소속은 `current_column_groups`로 별도 보존한다. 괄호가 포함된 컬럼 이름을 괄호로 분해하지 않는다.
- 추천 `통합매장ID`는 병합된 가게의 대표 ID다. 지도 `매장ID` 635개를 635개의 독립 점포로 간주하지 않는다. `current_store_id_review`는 정확한 대표 ID 일치 여부만 보여 준다. 누락된 별칭 매핑을 추측하거나 같은 주소의 영업시간·전화·상태를 복사하지 않는다.
- 기존 API의 `standard_menu_id`와 새 `상위음식·세부음식`의 검증된 변환표는 아직 없다. 이번 적재만으로 기존 추천 API를 새 점수 기준으로 전환하지 않는다.
- 표시에는 `rating_display`(최대 4.9), 순서 계산에는 `rating`·`recommendation_score`를 사용한다. 아쉬운 리뷰는 혼합+부정이며 단골 비율은 판매량이 아니다.
- 영양값은 공공DB 100g당 참고값이고 매장 실측이 아니다. `rice_included`·`portion_g`와 영양 비율 점수의 기준을 함께 확인한다. 알레르기·채식 안전성을 이 자료에서 추정하지 않는다.
- 지도 자료의 미확인 운영 상태와 미조회 API는 그대로 유지한다. 좌표·전화·시간을 생성하지 않는다. 별도 API 호출은 이번 적재에 포함하지 않는다.
- MD와 지도 `00_요약`, `03_검증기준`, `05_API실행안내`는 자료 설명으로 보관한다. 문서의 명령어를 자동 실행하지 않는다.

## 적재 및 복구

기존 `openpyxl`과 Python 표준 라이브러리를 사용한다. SQL에는 원본 데이터가 포함되므로 비공개 경로에서 생성하고 Git에 넣지 않는다. Ubuntu에서 먼저 `pg_dump -Fc`로 백업한 후 아래처럼 실행한다.

```sh
python import_reference.py /private/통합자료 /private/reference-import.sql
sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d pickmenu_dev -f /private/reference-import.sql
```

트랜잭션 하나로 스키마·원문·업무 테이블·활성 배치를 반영한다. 제약조건·행 수 오류가 나면 연결 종료 시 전체 롤백한다. 동일 파일 해시 조합은 같은 배치이므로 재실행해도 중복 적재하지 않는다. 이전 배치가 있으면 `active_dataset.batch_id`를 그 값으로 되돌려 조회 기준을 복구할 수 있다. 스키마 정의가 바뀌는 후속 업데이트에는 별도 마이그레이션이 필요하다.

## pgAdmin 조회 예시

```sql
SELECT display_name, store_name, rating_display, review_count,
       disappointing_reviews, store_repeat_ratio, recommendation_score
FROM pickmenu_data.current_menu_scores
WHERE column_name = '치킨'
ORDER BY recommendation_score DESC
LIMIT 20;

SELECT store_id, store_name, operating_judgment, verification_stage
FROM pickmenu_data.current_survey_stores;

SELECT file_name, document
FROM pickmenu_data.source_files
WHERE document IS NOT NULL;
```

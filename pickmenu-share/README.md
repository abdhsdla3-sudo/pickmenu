# 픽메뉴 공유용 배포본

로컬 픽메뉴 화면과 메뉴 5,736개의 집계·공개 영양 참고값을 Sites Worker에서 제공한다. `catalog.json`은 배포 시 서버 코드에 포함된다. 브라우저는 카테고리, 추천 결과, 선택한 메뉴 상세를 API로 조회한다. 원본 리뷰, 리뷰 ID, 매장 ID, 매장별 세부 집계와 SQLite 파일은 내보내기 대상에서 제외한다.

Windows CMD에서 로컬 픽메뉴 서버를 실행한 뒤 저장소 루트에서 다음 명령으로 공유용 데이터를 갱신하고 결과를 비교할 수 있다.

```bat
cd pickmenu-share
python -X utf8 export.py
node build.mjs --local
node --check dist\server\index.js
node verify.mjs
```

`export.py`는 옆 폴더의 로컬 데모에서 화면 4개와 공개에 필요한 필드만 복사한다. 카탈로그가 기본 경로에 없으면 먼저 `PICKMENU_CATALOG_PATH`를 지정한다. 로컬 추천 정책을 바꾸면 `worker.js`에 반영하고 결과를 비교한 뒤 배포한다. 실제 배포는 기존 Sites 체크아웃에서 원래 프로젝트 ID를 사용한다. [배포 절차](../docs/architecture.md)

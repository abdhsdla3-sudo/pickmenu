# 버전과 릴리스

변경은 GitHub `abdhsdla3-sudo/pickmenu`의 `main`을 기준으로 관리한다. 릴리스 하나는 검증한 커밋 하나와 연결한다. 코드·설정·데이터 계약을 바꾸는 업데이트에는 다음 규칙을 적용한다.

| 변경 | 올릴 자리 | 예 |
| --- | --- | --- |
| 오류 수정, 작은 개선 | PATCH | 0.1.0 → 0.1.1 |
| 기존 호출과 호환되는 기능 추가 | MINOR | 0.1.1 → 0.2.0 |
| 기존 호출과 호환되지 않는 계약 변경 | MAJOR | 0.2.0 → 1.0.0 |

1. 변경과 함께 `VERSION`, `CHANGELOG.md`에 새 번호, 사용자 영향, 데이터·설정 마이그레이션을 기록한다.
2. 관련 검사를 실행하고 변경 파일을 검토한다. `git diff --cached --name-only`에서 원본 리뷰, 인증 정보, 생성 데이터, 모델 파일이 없는지 확인한다.
3. 검증한 커밋을 GitHub에 반영한다. 공동 작업에서는 PR을 병합한 뒤 병합 커밋으로 돌아와 아래 명령을 실행한다. 예시는 다음 패치인 0.1.1이다.

```bat
git fetch origin
git switch main
git pull --ff-only origin main
git tag -a v0.1.1 -m "픽메뉴 0.1.1"
git push origin refs/tags/v0.1.1
gh release create v0.1.1 --verify-tag --title "픽메뉴 0.1.1" --notes-file CHANGELOG.md
```

실제 릴리스에서는 해당 버전의 항목만 담은 UTF-8 파일을 `--notes-file`에 넘길 수 있다. 이미 공개한 태그를 고치는 대신 새 PATCH를 만든다. 배포한 버전은 서버와 Sites에서 확인하며 GitHub Release에는 배포 여부를 별도로 기록한다.

"""2차 보완 자료를 전수 검사하고 기존 원본 추적을 보존한 새 카탈로그를 만든다."""

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

import openpyxl

from recommend import compact


def table(path: Path, sheet: str):
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = book[sheet].iter_rows(values_only=True)
        headers = next(rows)
        for row in rows:
            if any(value is not None for value in row):
                yield dict(zip(headers, row))
    finally:
        book.close()


def unique(source: Path, prefix: str) -> Path:
    paths = list(source.glob(prefix + '*.xlsx'))
    if len(paths) != 1:
        raise ValueError(f'{prefix}: 파일이 정확히 1개 필요합니다. 발견 {len(paths)}개')
    return paths[0]


def inspect_files(source: Path) -> list[dict[str, Any]]:
    inventory = []
    for path in sorted(source.iterdir()):
        if path.suffix not in {'.xlsx', '.md'}:
            continue
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        entry: dict[str, Any] = {'file': path.name, 'bytes': path.stat().st_size, 'sha256': digest}
        if path.suffix == '.md':
            content = path.read_text(encoding='utf-8-sig')
            entry.update(lines=len(content.splitlines()), usage='해석 참고 문서; 명령 실행 안 함')
        else:
            book = openpyxl.load_workbook(path, read_only=True, data_only=True)
            sheets = []
            try:
                for sheet in book:
                    counts: Counter[str] = Counter()
                    rows = sheet.iter_rows(values_only=True)
                    next(rows, None)
                    for row in rows:
                        counts['input'] += 1
                        counts['processed' if any(v is not None for v in row) else 'blank'] += 1
                    sheets.append({'sheet': sheet.title, **dict(counts)})
            finally:
                book.close()
            entry.update(tables=sheets, usage='전수 구조·행수 검사; 메뉴 정규화/영양 보완본은 아래 정책 적용')
        inventory.append(entry)
    return inventory


def revise(menu: dict[str, Any], rows: list[dict[str, Any]],
           originals: set[str]) -> tuple[dict[str, Any] | None, str]:
    if not rows:
        return None, '최신 정규화 연결 없음'
    if not all(r['①메뉴역할'] == '음식' and r['식사추천후보'] == '참고후보(사람미검수)'
               for r in rows):
        return None, '최신 자료의 제외·복합·미확정 또는 역할 충돌'
    if not originals <= {str(r['원본메뉴명']).strip() for r in rows}:
        return None, '일부 원본 메뉴의 최신 연결 미확인'
    result = dict(menu)
    names = {r['②정리메뉴명'] for r in rows if r['②정리메뉴명']}
    if len(names) == 1:
        result['name'] = next(iter(names))
    # 기존 수치의 출처 대조와 별개로 최신 음식 분류와의 의미 일치를 확인한다.
    if not all(all(compact(c) in {compact(str(r[k])) for k in ('③상위음식명', '④세부음식명') if r[k]}
                   for c in menu['components']) for r in rows):
        result.update(nutrition=None, references=[], nutrition_status='최신 음식 분류와 영양 연결 재검토 필요')
    result['mapping_method'] = '2차 보완 정규화 · 자동 분류 · 사람 미검수'
    return result, '최신 참고후보 적용'


def refresh(source_db: Path, updates: Path, output: Path) -> dict[str, Any]:
    if source_db.resolve() == output.resolve():
        raise ValueError('원본 보존을 위해 출력 DB는 다른 경로여야 합니다.')
    inventory = inspect_files(updates)
    norm = unique(updates, '픽메뉴_1~4단계_메뉴정규화_2차보완')
    nutr = unique(updates, '픽메뉴_5단계_공공영양정보_연결후보_2차보완')
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    previous = {r['원본메뉴고유키']: r for r in table(
        unique(updates, '픽메뉴_정규화_1~4단계_보완검증본'), '01_메뉴정규화')}
    for row in table(norm, '01_메뉴정규화'):
        if row['원본메뉴고유키'] in seen:
            raise ValueError('정규화 고유키 중복')
        seen.add(row['원본메뉴고유키'])
        grouped[row['기존표준메뉴ID']].append(row)
        counts['normalization_rows'] += 1
        counts['normalization_occurrences'] += int(row['출현행수'])
        before = previous.get(row['원본메뉴고유키'])
        if before is None or any(before[k] != row[k] for k in
                                 ('원본메뉴명', '기존표준메뉴ID', '출현행수')):
            raise ValueError('보존 대상 원본 식별자·출현행수가 변경되었습니다.')
        counts['normalization_changed_rows'] += any(before.get(k) != v for k, v in row.items())
    if seen != previous.keys():
        raise ValueError('정규화 변경 전후 고유키가 다릅니다.')
    nutrition_methods: Counter[str] = Counter()
    nutrition_keys: set[str] = set()
    for row in table(nutr, '01_메뉴별_영양연결'):
        nutrition_keys.add(row['원본메뉴고유키'])
        nutrition_methods[str(row['⑦보완방법'] or '기존 연결')] += 1
        counts['nutrition_rows'] += 1
        counts['nutrition_energy_present'] += row['⑤열량_kcal_후보'] is not None
    if nutrition_keys != seen or counts['nutrition_rows'] != len(seen):
        raise ValueError('정규화와 영양 고유키가 일치하지 않습니다.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.sqlite3', delete=False) as temp:
        temporary = Path(temp.name)
    changes: Counter[str] = Counter()
    try:
        with closing(sqlite3.connect(source_db.as_uri() + '?mode=ro', uri=True)) as src, closing(
                sqlite3.connect(temporary)) as db:
            src.backup(db)
            db.execute('DROP TABLE IF EXISTS update_mapping')
            db.execute('CREATE TABLE update_mapping(menu_id TEXT, original_key TEXT PRIMARY KEY, '
                       'original_name TEXT, cleaned_name TEXT, role TEXT, status TEXT, source TEXT)')
            for mid, rows in grouped.items():
                for row in rows:
                    db.execute('INSERT INTO update_mapping VALUES(?,?,?,?,?,?,?)',
                               (mid, row['원본메뉴고유키'], row['원본메뉴명'], row['②정리메뉴명'],
                                row['①메뉴역할'], row['식사추천후보'], norm.name))
            for mid, payload in src.execute('SELECT id,data FROM catalog ORDER BY id'):
                menu = json.loads(payload)
                originals = {r[0].strip() for r in src.execute(
                    'SELECT DISTINCT original_name FROM trace WHERE menu_id=?', (mid,))}
                revised, reason = revise(menu, grouped.get(mid, []), originals)
                changes[reason] += 1
                counts['catalog_input'] += 1
                if revised is None:
                    db.execute('DELETE FROM catalog WHERE id=?', (mid,))
                    counts['catalog_excluded'] += 1
                    continue
                counts['catalog_output'] += 1
                counts['renamed'] += menu['name'] != revised['name']
                counts['nutrition_removed_for_taxonomy'] += bool(menu['references']) and not revised['references']
                counts['scored_nutrition'] += revised['nutrition'] is not None
                db.execute('UPDATE catalog SET data=? WHERE id=?',
                           (json.dumps(revised, ensure_ascii=False), mid))
            if not counts['catalog_output']:
                raise ValueError('갱신 후 추천 후보가 없습니다.')
            counts['mapped_source_keys'] = db.execute(
                'SELECT count(*) FROM update_mapping JOIN catalog ON menu_id=id').fetchone()[0]
            counts['source_keys_not_recommended'] = len(seen) - counts['mapped_source_keys']
            db.commit()
            if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise ValueError('출력 DB 무결성 검사 실패')
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    report = {'counts': dict(counts), 'catalog_decisions': dict(changes),
              'nutrition_methods': dict(nutrition_methods), 'inventory': inventory,
              'nutrition_policy': '2차 보완 영양 후보·중앙값·AI 추정치는 미승인으로 반영 안 함. '
              '기존 원수치 대조 참고값도 최신 음식 분류 불일치 시 해제. 원본 추적 테이블 보존.'}
    output.with_suffix('.audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db', type=Path, required=True)
    parser.add_argument('--updates', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = refresh(args.source_db.resolve(), args.updates.resolve(), args.output.resolve())
    print(json.dumps(result['counts'], ensure_ascii=False))

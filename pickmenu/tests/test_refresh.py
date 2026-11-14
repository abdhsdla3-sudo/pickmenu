import unittest

from refresh import revise


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.menu = {'name': '김밥)', 'components': ['김밥'], 'nutrition': {'values': [1]},
                     'references': [{'food_id': 'REFERENCE'}], 'nutrition_status': '참고'}
        self.row = {'①메뉴역할': '음식', '식사추천후보': '참고후보(사람미검수)',
                    '원본메뉴명': '김밥)', '②정리메뉴명': '김밥',
                    '③상위음식명': '김밥', '④세부음식명': '김밥'}

    def test_name_correction_and_reference_preservation(self):
        updated, _ = revise(self.menu, [self.row], {'김밥)'})
        self.assertEqual(updated['name'], '김밥')
        self.assertEqual(updated['nutrition'], self.menu['nutrition'])
        self.assertEqual(self.menu['name'], '김밥)')

    def test_latest_exclusion_missing_trace_and_conflict(self):
        for rows, originals in (([], {'김밥)'}),
                                ([self.row], {'새 원본'}),
                                ([self.row, {**self.row, '①메뉴역할': '비음식·파싱오류'}], {'김밥)'}),
                                ([{**self.row, '식사추천후보': '제외(부가항목·복합·미확정)'}], {'김밥)'})):
            with self.subTest(rows=rows):
                self.assertIsNone(revise(self.menu, rows, originals)[0])

    def test_taxonomy_change_invalidates_old_nutrition(self):
        row = {**self.row, '③상위음식명': '만두', '④세부음식명': '고기만두'}
        updated, _ = revise(self.menu, [row], {'김밥)'})
        self.assertIsNone(updated['nutrition'])
        self.assertEqual(updated['references'], [])

    def test_ambiguous_name_is_not_chosen_arbitrarily(self):
        updated, _ = revise(self.menu, [self.row, {**self.row, '②정리메뉴명': '다른 이름'}], {'김밥)'})
        self.assertEqual(updated['name'], self.menu['name'])

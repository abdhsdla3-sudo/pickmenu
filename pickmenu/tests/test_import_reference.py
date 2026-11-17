"""원본 형식과 SQL 문자열 경계를 검증한다. 실제 적재는 PostgreSQL 트랜잭션으로 검증한다."""

import unittest

from import_reference import literal, validate_types


class ReferenceImportTest(unittest.TestCase):
    def test_integer_accepts_integral_csv_float_but_rejects_fraction(self):
        validate_types([{"순위": "1.0"}], "rank:순위:integer")
        with self.assertRaises(ValueError):
            validate_types([{"순위": "1.5"}], "rank:순위:integer")

    def test_numeric_rejects_nonfinite_and_preserves_missing(self):
        validate_types([{"점수": ""}, {"점수": None}], "score:점수:numeric")
        for value in ["NaN", "Infinity", "not-a-number"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_types([{"점수": value}], "score:점수:numeric")

    def test_boolean_does_not_treat_unknown_status_as_false(self):
        validate_types([{"표시": "예"}, {"표시": "False"}], "visible:표시:boolean")
        with self.assertRaises(ValueError):
            validate_types([{"표시": "미확인"}], "visible:표시:boolean")

    def test_missing_column_and_sql_literal_boundary(self):
        with self.assertRaises(ValueError):
            validate_types([{}], "rank:순위:integer")
        self.assertEqual(literal("한글'); DROP TABLE users; --"),
                         "'한글''); DROP TABLE users; --'")
        with self.assertRaises(ValueError):
            literal("bad\0text")

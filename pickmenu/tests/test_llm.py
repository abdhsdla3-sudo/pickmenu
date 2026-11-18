import os
import unittest
from unittest.mock import patch

import llm


class LLMTest(unittest.TestCase):
    def test_conditions_preserve_restrictions_and_validate(self):
        payload = {"messages": ["매콤한 국물, 새우 알레르기"], "profile": {"allergy": "땅콩", "diet": "vegan"}}
        with patch.object(llm, "generate", return_value={"spice": "hot", "soup": "yes",
                                                        "allergy": "새우", "diet": "general"}):
            profile, _ = llm.conditions(payload, [])
        self.assertEqual(profile.diet, "vegan")
        self.assertEqual(profile.allergy, "땅콩,새우")
        self.assertEqual(profile.spice, "hot")
        with patch.object(llm, "generate", return_value={"query": []}):
            with self.assertRaises(llm.Unavailable):
                llm.conditions(payload, [])

    def test_invalid_input_never_calls_model(self):
        for messages in ([], [3], ["x" * 301], ["x"] * 7, ["a\nb"]):
            with self.subTest(messages=messages), patch.object(llm, "generate") as generate:
                with self.assertRaises(ValueError):
                    llm.conditions({"messages": messages, "profile": {}}, [])
                generate.assert_not_called()

    def test_unspoken_conditions_and_negation(self):
        with patch.object(llm, "generate", return_value={"goal": "balance", "spice": "hot",
                                                        "soup": "yes", "meal_category": "해산물"}):
            profile, _ = llm.conditions({"messages": ["가볍고 안 매운 음식, 국물 없는 메뉴"],
                                         "profile": {}}, ["해산물"])
        self.assertEqual((profile.goal, profile.spice, profile.soup, profile.meal_category),
                         ("light", "mild", "no", ""))

    def test_busy_and_disabled_do_not_open_network(self):
        with patch.dict(os.environ, {"PICKMENU_LLM_URL": ""}), patch.object(llm, "urlopen") as network:
            with self.assertRaises(llm.Unavailable):
                llm.generate("", {})
            network.assert_not_called()
        llm._slot.acquire()
        try:
            with patch.dict(os.environ, {"PICKMENU_LLM_URL": "http://127.0.0.1:11435"}):
                with self.assertRaises(llm.Unavailable):
                    llm.generate("", {})
        finally:
            llm._slot.release()

    def test_store_reasons_fall_back_and_limit(self):
        def row(name, reviews):
            return {"menu_name": name, "store_name": f"{name}집", "district": "권선구", "reviews": reviews}
        result = {"citywide": False, "stores": [row(f"메뉴{i}", 7 + i) for i in range(12)]}
        with patch.object(llm, "generate", side_effect=llm.Unavailable):
            llm.store_comments(result)
        self.assertNotIn("comment", result["stores"][10])
        self.assertIn("가장 많이", result["stores"][9]["comment"])  # 리뷰가 가장 많은 가게
        self.assertTrue(all("comment" in r for r in result["stores"][:10]))
        self.assertTrue(any("8건" in r["comment"] for r in result["stores"][:10]))

    def test_store_reasons_keep_only_safe_ai_text(self):
        stores = [{"menu_name": "불향가득제육", "store_name": "제육대가", "district": "영통구", "reviews": 120},
                  {"menu_name": "매운 제육", "store_name": "한끼식당", "district": "영통구", "reviews": 30}]
        written = {"0": "퇴근길 지친 저녁, 불향 한 접시로 하루를 마무리해 보세요. 리뷰 120건이 먼저 다녀갔어요.",
                   "1": "제육대가보다 저렴하고 배달도 빠른 집이에요."}
        result = {"citywide": False, "stores": [dict(s) for s in stores]}
        with patch.object(llm, "generate", return_value=written) as generate:
            llm.store_comments(result)
        self.assertEqual(generate.call_args.kwargs["temperature"], 0.8)
        self.assertEqual(result["stores"][0]["comment"], written["0"])
        self.assertNotIn("저렴", result["stores"][1]["comment"])  # 금지어·다른 가게 이름이면 기본 문장
        self.assertTrue(llm.safe_reason("리뷰 120건이 쌓인 든든한 한 끼예요.", stores[0], set()))
        self.assertFalse(llm.safe_reason("리뷰 999건이 쌓인 든든한 한 끼예요.", stores[0], set()))
        self.assertFalse(llm.safe_reason("이 동네 최고의 맛집이에요, 꼭 드셔 보세요.", stores[0], set()))
        self.assertTrue(llm.safe_reason("최상급 기분을 내는 불향가득제육 한 접시예요.", stores[0], set()))
        self.assertFalse(llm.safe_reason("간장의 깊이가 매력적인 불향가득제육이에요.", stores[0], set()))
        self.assertFalse(llm.safe_reason("120명이 고른 불향 한 접시로 저녁을 열어 보세요.", stores[0], set()))
        self.assertFalse(llm.safe_reason("늦은 밤 두루치기로 푸근한 밥 한 쟁抓好하세요.", stores[0], set()))
        self.assertFalse(llm.safe_reason("새로 오픈한 매장이라 호기심으로 들러 보세요.", stores[0], set()))
        few = {"citywide": False, "stores": [{"menu_name": "짬뽕", "store_name": "만빈원", "district": "팔달구",
                                              "reviews": 2}]}
        with patch.object(llm, "generate") as generate:
            llm.store_comments(few)
        generate.assert_not_called()  # 리뷰 5건 미만만 있으면 AI를 부르지 않는다
        self.assertIn("아직 적지만", few["stores"][0]["comment"])
        plain = {"menu_name": "콤비네이션피자", "store_name": "피자집", "district": "권선구", "reviews": 9}
        self.assertFalse(llm.safe_reason("매콤한 기분 전환이 필요한 날의 한 판이에요.", plain, set()))
        self.assertTrue(llm.safe_reason("매콤한 기분 전환이 필요한 날 어울려요.", stores[1], set()))

    def test_empty_results_do_not_prompt_model(self):
        with patch.object(llm, "generate") as generate:
            self.assertIn("찾지 못했", llm.reply({"families": []}))
            generate.assert_not_called()

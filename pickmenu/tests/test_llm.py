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

    def test_negation_and_soup_scope(self):
        # 1.6.5: 이야기 추천 548문장 실험에서 반대로 들어가던 표현
        cases = [("매운 건 싫어", {"spice": "hot"}, "spice", "mild"),
                 ("맵찔이라 매운 건 못 먹어", {"spice": "hot"}, "spice", "mild"),
                 ("매운 건 못 먹고 국물 있는 걸로", {"spice": "hot"}, "spice", "mild"),
                 ("안 매운 거 말고 매운 거", {}, "spice", "hot"),
                 ("얼큰한 국물", {"soup": "yes"}, "spice", "hot"),
                 ("국물업는거", {"soup": "yes"}, "soup", "no"),
                 ("국물 요리로, 피자, 파 싫어", {"soup": "no"}, "soup", "yes"),
                 ("국물 있는 거, 당근는 빼 줘", {"soup": "no"}, "soup", "yes"),
                 ("탕 말고 볶음 종류", {}, "soup", "no"),
                 ("국물은 빼고", {}, "soup", "no"),
                 ("국물 안 좋아해", {}, "soup", "no")]
        for text, model, key, want in cases:
            with self.subTest(text=text), patch.object(llm, "generate", return_value=dict(model)):
                profile, _ = llm.conditions({"messages": [text], "profile": {}}, [])
                self.assertEqual(getattr(profile, key), want)

    def test_category_kept_when_words_point_to_it(self):
        cats = ["국밥·해장국", "족발·보쌈", "밥·덮밥", "해산물", "찌개·탕", "닭요리", "면"]
        for text, model, want in [("국밥 중에서", "국밥·해장국", "국밥·해장국"),
                                  ("족발 쪽으로", "족발·보쌈", "족발·보쌈"),
                                  ("면 말고 밥", "밥·덮밥", "밥·덮밥"),
                                  ("국물 없는 거", "국밥·해장국", ""),     # 말하지 않은 음식군
                                  ("뜨끈한 거 먹고 싶다", "찌개·탕", ""),
                                  ("국밥 먹고 싶어", "없는음식군", ""), ("닭요리 먹고 싶어", "", "닭요리"),
                                  ("국수", "", "면"), ("해장하고 싶어", "", "국밥·해장국")]:
            with self.subTest(text=text), patch.object(llm, "generate", return_value={"meal_category": model}):
                profile, _ = llm.conditions({"messages": [text], "profile": {}}, cats)
                self.assertEqual(profile.meal_category, want)

    def test_ingredients_need_a_cue(self):
        for text, model, allergy, exclude in [
                ("음 회 땡겨 그리고 밀 알레르기 있어", {"allergy": "밀", "exclude": "음"}, "밀", ""),
                ("점심으로 우유 알레르기 있어, 고기 요리", {"allergy": "우유", "exclude": "고기"}, "우유", ""),
                ("짬뽕 먹고 싶어 근데 안 매운 걸로", {"exclude": "매운"}, "", ""),
                ("채식주의자야", {"allergy": "채식주의자"}, "", ""),
                ("오이랑 고수 둘 다 싫어", {"exclude": "오이, 고수"}, "", "오이,고수"),
                ("파는 빼 줘! 새우 알레르기 있어", {"exclude": "새우"}, "새우", "파"),
                ("게 알레르기 있어 오이 싫어", {"allergy": "오이"}, "게", "오이"),
                ("새우 알레르기 있어 가지 안 들어간 걸로", {"allergy": "새우"}, "새우", "가지"),
                ("라면 먹고 싶은데 가지 싫어", {"exclude": "라면"}, "", "가지"),
                ("기름진 건 별로", {"exclude": "기름진"}, "", ""),
                ("곱창볶음 매운 건 못 먹어", {"exclude": "곱창"}, "", ""),
                ("칼칼한 거 / 아니 매운 건 못 먹어", {"exclude": "매운"}, "", ""),
                ("회 먹고 싶다 근데 날것은 별로", {"exclude": "날것"}, "", "날것")]:
            with self.subTest(text=text), patch.object(llm, "generate", return_value=dict(model)):
                profile, _ = llm.conditions({"messages": [text], "profile": {}}, [])
                self.assertEqual((profile.allergy, profile.exclude), (allergy, exclude))

    def test_explicit_restrictions_survive_empty_model_output(self):
        with patch.object(llm, "generate", return_value={}):
            profile, _ = llm.conditions({"messages": ["오늘은 비건이야", "영통구에서 국물은 필요 없어",
                                                      "새우는 못 먹어", "해물은 피하고 싶어요", "우유 알레르기 있어"],
                                         "profile": {}}, [])
        self.assertEqual((profile.diet, profile.district, profile.soup), ("vegan", "영통구", "no"))
        self.assertEqual((profile.exclude, profile.allergy), ("새우,해물", "우유"))

    def test_query_only_menu_names(self):
        for text, query, want in [("짬뽕 먹고 싶어", "짬뽕", "짬뽕"), ("시원한 거", "시원한 거", ""),
                                  ("야식으로 먹을 만한 거", "야식으로 먹을 만한 거", ""),
                                  ("싸고 양 많은 거", "싸고 양 많은 거", ""), ("비건 메뉴 추천해 줘", "비건 메뉴", "비건"),
                                  ("탕 말고 볶음 종류", "볶음 종류", "볶음"), ("돈까스", "돈가스", ""),
                                  ("국밥 중에서", "국밥 중에서", "국밥"), ("족발 쪽으로", "족발 쪽으로", "족발"),
                                  ("국물 없는 거", "국물 없는", ""), ("단백질 위주로", "단백질 위주", ""),
                                  ("고단백으로 뭐 있어?", "고단백", ""), ("다이어트 중이야", "다이어트", ""),
                                  ("골고루 균형 잡힌 걸로", "골고루 균형 잡힌 걸로", ""),
                                  ("채식주의자야 권선구에서", "채식주의자야 권선구에서", ""),
                                  ("채식주의자야 권선구에서", "권선구", "")]:
            with self.subTest(text=text), patch.object(llm, "generate", return_value={"query": query}):
                profile, _ = llm.conditions({"messages": [text], "profile": {}}, [])
                self.assertEqual(profile.query, want)

    def test_taste_words(self):
        for text, spice in [("맵고 짜고 단거", "hot"), ("매운거, 짠거, 단거", "hot"), ("안 맵고 짠 거", "mild"),
                            ("덜 매운 걸로", "mild"), ("짠 거", "")]:
            with self.subTest(text=text), patch.object(llm, "generate", return_value={}):
                self.assertEqual(llm.conditions({"messages": [text], "profile": {}}, [])[0].spice, spice)
        self.assertEqual(llm.tastes("맵고 짜고 단거"), "sweet,salty")
        self.assertEqual(llm.tastes("새콤하고 바삭한 거, 느끼한 건 싫어"), "sour,-rich,crispy")
        self.assertEqual(llm.tastes("담백하고 시원한 국물"), "plain,cool")
        self.assertEqual(llm.tastes("단백질 위주로 간단하게"), "")
        self.assertEqual(llm.tastes("단 건 싫어"), "-sweet")
        self.assertEqual(llm.tastes("단고 고소한 거"), "sweet,nutty")
        self.assertEqual(llm.tastes("느끼하지 않고 담백한 거"), "plain,-rich")
        self.assertEqual(llm.menu_query("새콤한"), "")
        self.assertIn("짭짤한 맛", llm.taste_note(["맵고 짜고 단거"]))
        self.assertEqual(llm.taste_note(["단백질 위주로"]), "")
        with patch.object(llm, "generate", return_value={}):
            self.assertEqual(llm.conditions({"messages": ["고소하고 쫄깃한 거"], "profile": {}}, [])[0].taste, "nutty,chewy")

    def test_reply_warns_when_ingredients_unchecked(self):
        family = {"name": "두부덮밥", "best": {"reasons": [], "needs_check": True}}
        with patch.object(llm, "generate", side_effect=llm.Unavailable("x")):
            self.assertIn("가게에 꼭 확인", llm.reply({"families": [family]}))
            self.assertNotIn("가게에 꼭 확인", llm.reply({"families": [{**family, "best": {"reasons": []}}]}))

    def test_named_food(self):
        foods = {"햄버거": {"버거·샌드위치"}, "치킨": {"치킨"}, "치킨마요덮밥": {"밥·덮밥"}, "피자": {"피자"},
                 "짜장면": {"면", "중식"}, "회": {"해산물"}, "김밥": {"분식"}, "감자탕": {"찌개·탕"}}
        for text, food in [("햄버거 먹고 싶어", "햄버거"), ("버거 추천해줘", "햄버거"), ("치킨마요덮밥 있어?", "치킨마요덮밥"),
                           ("햄버거 말고 피자", "피자"), ("치킨은 싫어", ""), ("짜장 땡긴다", "짜장면"), ("회사 근처", "")]:
            with self.subTest(text=text):
                self.assertEqual(llm.named_food(text, list(foods)), food)
        with patch.object(llm, "generate", return_value={"meal_category": "버거·샌드위치"}):
            profile, _ = llm.conditions({"messages": ["햄버거 먹고 싶어"], "profile": {}}, ["버거·샌드위치"], foods)
        self.assertEqual((profile.query, profile.meal_category), ("햄버거", "버거·샌드위치"))
        # 메뉴가 속하지 않은 음식군은 버리고, 메뉴 이름의 '탕'은 국물 조건이 아니다.
        with patch.object(llm, "generate", return_value={"meal_category": "밥·덮밥"}):
            profile, _ = llm.conditions({"messages": ["김밥 먹고 싶어"], "profile": {}}, ["밥·덮밥", "분식"], foods)
        self.assertEqual((profile.query, profile.meal_category), ("김밥", ""))
        with patch.object(llm, "generate", return_value={}):
            profile, _ = llm.conditions({"messages": ["감자탕 땡긴다"], "profile": {}}, [], foods)
            self.assertEqual((profile.query, profile.soup), ("감자탕", ""))
            profile, _ = llm.conditions({"messages": ["얼큰한 감자탕 국물 있는 걸로"], "profile": {}}, [], foods)
            self.assertEqual((profile.spice, profile.soup), ("hot", "yes"))
        foods = {**foods, "양념치킨": {"치킨"}, "칼국수": {"면"}, "돈가스": {"양식"}, "죽": {"죽"}, "전": {"전·부침"}}
        for messages, model, query in [(["양념치킨 추천해줘"], {"query": "치킨"}, "양념치킨"),
                                       (["돈가스 추천해줘", "매운 건 못 먹어", "아니 칼국수로 바꿔줘"], {"query": "돈가스"}, "칼국수"),
                                       (["동남아 음식 추천"], {"query": "동남아", "meal_category": "아시안"}, ""),
                                       (["칼국수 땡긴다", "아니 죽으로 바꿔줘"], {"query": "칼국수"}, "죽"),
                                       (["전 매운 거 좋아해"], {}, "")]:
            with self.subTest(messages=messages), patch.object(llm, "generate", return_value=model):
                profile, _ = llm.conditions({"messages": messages, "profile": {}}, ["아시안", "면", "치킨"], foods)
                self.assertEqual(profile.query, query)

    def test_reply_mentions_relaxed(self):
        family = {"name": "비빔밥", "best": {"reasons": []}}
        with patch.object(llm, "generate", side_effect=llm.Unavailable("x")):
            self.assertIn("국물 조건을 빼고", llm.reply({"families": [family], "relaxed": ["국물 조건"]}))

    def test_reply_particle(self):
        def fam(name):
            return {"name": name, "best": {"reasons": []}}
        with patch.object(llm, "generate", side_effect=llm.Unavailable):
            self.assertIn("물회를 골랐어요", llm.reply({"families": [fam("국밥"), fam("물회")]}))
            self.assertIn("국밥을 골랐어요", llm.reply({"families": [fam("물회"), fam("국밥")]}))

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

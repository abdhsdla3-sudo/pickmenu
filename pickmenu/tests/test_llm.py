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

    def test_past_meals_are_not_dinner_requests(self):
        foods = {"짜장면": {"중식"}, "순대국밥": {"국밥·해장국"}, "칼국수": {"면"}}
        categories = ["중식", "국밥·해장국", "면"]
        cases = [
            (["어제는 짜장면을 먹었고, 오늘 점심으로는 순대국밥을 먹었어. "
              "오늘 저녁은 좀 가볍고 기름지지 않은 음식을 먹고 싶어."], "", "짜장면,순대국밥", "light", "-rich"),
            (["어제는 짜장면, 오늘 점심엔 매운 순대국밥을 먹었는데 저녁엔 칼국수 먹고 싶어"],
             "칼국수", "짜장면,순대국밥", "balance", ""),
            (["점심엔 순대국밥 먹었어", "저녁에도 순대국밥으로 골라줘"], "순대국밥", "", "balance", ""),
            (["어제 먹은 순대국밥을 저녁에 또 먹고 싶어"], "순대국밥", "", "balance", ""),
            (["점심에 순대국밥을 안 먹었어. 저녁은 가벼운 걸로"], "", "", "light", ""),
        ]
        for messages, query, avoided, goal, taste in cases:
            with self.subTest(messages=messages), patch.object(llm, "generate", return_value={
                "query": "순대국밥", "meal_category": "국밥·해장국", "spice": "hot",
            }):
                profile, _ = llm.conditions({"messages": messages, "profile": {}}, categories, foods)
                self.assertEqual((profile.query, profile.avoid_menus, profile.goal, profile.taste, profile.spice),
                                 (query, avoided, goal, taste, ""))
                if query != "순대국밥":
                    self.assertNotEqual(profile.meal_category, "국밥·해장국")

    def test_reply_distinguishes_measured_fat_and_name_cues(self):
        info = {"basis": 100, "unit": "g", "values": [120, 10, 15, 2, None]}
        result = {"families": [
            {"name": "비빔밥", "best": {"reasons": [], "nutrition": info, "components": ["비빔밥"]}},
            {"name": "두부 요리", "best": {"reasons": [], "nutrition": None,
                                      "components": ["두부 요리"], "cooking_method": "찌기"}},
            {"name": "고구마튀김", "best": {"reasons": [], "nutrition": None, "components": ["고구마튀김"]}},
        ]}
        with patch.object(llm, "generate", return_value={}):
            reply = llm.reply(result, taste="-rich")
        self.assertIn("100g당 지방 참고값", reply)
        self.assertIn("지방 참고값이 없는 메뉴", reply)
        self.assertIn("공개 참고 레시피", reply)
        self.assertIn("이름에서 조리법을 추정", reply)

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
                positive, _ = llm.food_mentions(text, list(foods))
                self.assertEqual(positive[-1] if positive else "", food)
        foods.update({"짬뽕": {"중식"}, "짬뽕밥": {"중식"}})
        with patch.object(llm, "generate", return_value={"query": "짬뽕"}):
            result, _ = llm.conditions({"messages": ["짬뽕밥 추천해 줘", "짬뽕밥을 빼고"],
                                       "profile": {"query": "짬뽕밥"}}, ["중식"], foods)
        self.assertEqual((result.query, result.avoid_menus), ("", "짬뽕밥"))
        self.assertEqual(llm.food_mentions("짬뽕밥 빼고 짬뽕 먹을래", list(foods)), (["짬뽕"], ["짬뽕밥"]))
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

    def test_heldout_rules(self):
        # 새 말투 시험셋에서 나온 지역·부정·돌려 말하기 표현
        cats = ["국밥·해장국", "전·부침", "치킨", "면", "분식"]
        cases = [
            (["장안구 사는데 국물 없는 걸로"], {}, "district", "장안구"),
            (["권선구에 칼국수 집 있나?"], {}, "district", "권선구"),
            (["영통구에서 먹을 거", "아 팔달구로 바꿀게"], {}, "district", "팔달구"),
            (["영통구 말고 장안구"], {}, "district", "장안구"),
            (["매운 거 ㄴㄴ"], {}, "spice", "mild"),
            (["매운 건 괜찮은데 너무 짠 건 싫어"], {}, "spice", ""),
            (["국물은 됐고 볶음밥"], {}, "soup", "no"),
            (["다이어트 3주차인데 오늘은 치팅데이라 치킨"], {}, "goal", "balance"),
            (["어제 술 마셔서 속이 쓰려"], {}, "meal_category", "국밥·해장국"),
            (["비 오니까 전 생각나네"], {}, "meal_category", "전·부침"),
            (["치킨 말고 다른 거"], {"meal_category": "치킨"}, "meal_category", ""),
            (["비건 친구랑 팔달구에서 만나"], {}, "diet", "vegan"),
            (["비건인데 국물 있는 거"], {}, "diet", "vegan"),
            (["분식 추천해줘"], {"query": "분식"}, "meal_category", "분식"),
        ]
        for messages, model, key, want in cases:
            with self.subTest(messages=messages), patch.object(llm, "generate", return_value=dict(model)):
                profile, _ = llm.conditions({"messages": messages, "profile": {}}, cats, {"떡볶이": {"분식"}})
                self.assertEqual(getattr(profile, key), want)
        self.assertEqual(llm.tastes("느끼한 건 영 아이다"), "-rich")
        self.assertEqual(llm.tastes("담백한 거 없을까?"), "plain")

    def test_model_fixed_menu_names(self):
        # 파인튜닝 모델이 오타·영어·앞 턴 꾸밈말을 대표 메뉴 이름으로 고친 경우는 받고, 근거 없는 이름은 버린다.
        foods = {name: {"분식"} for name in ("떡볶이", "치킨", "양념치킨", "피자", "부대찌개", "돈가스", "순대",
                                            "순대국밥", "김치찌개", "김치찜", "냉면", "비빔냉면", "죽", "볶음밥", "볶음면",
                                            "소불고기", "돼지불고기", "매운탕", "감자탕")}
        cases = [
            (["떡뽁이 먹고싶다"], "떡볶이", "떡볶이"),
            (["떡볶 ㄱ?"], "떡볶이", "떡볶이"),
            (["부대찌게ㄱㄱ"], "부대찌개", "부대찌개"),
            (["돈까쓰 땡김"], "돈가스", "돈가스"),
            (["pizza 먹고 싶어"], "피자", "피자"),
            (["pizza 먹고 싶어"], "돈가스", ""),
            (["hello"], "피자", ""),
            (["no pizza 오늘은"], "피자", ""),
            (["치킨!", "양념으로"], "양념치킨", "양념치킨"),
            (["치킨!", "양념 빼고"], "양념치킨", "치킨"),
            (["냉면", "비빔으로"], "비빔냉면", "비빔냉면"),
            (["순대국 먹을래"], "순대국밥", "순대국밥"),
            (["치킨 먹고 싶어"], "양념치킨", "치킨"),        # 꾸밈말이 없으면 말한 메뉴 그대로
            (["김치찜 먹고 싶어"], "김치찌개", "김치찜"),    # 말한 메뉴와 다른 메뉴로 바꾸지 않는다
            (["오늘 날씨 좋다"], "피자", ""),                 # 문장에 근거가 없다
            (["죽인데 매운 거"], "매운죽", "죽"),             # 없는 메뉴 이름
            (["죽 빼고 다른 거"], "매운죽", ""),
            (["떡뽁이 말고 다른 거"], "떡볶이", ""),
            # 글자 수가 다른 비슷한 말이나 여러 메뉴의 앞부분은 오타로 보지 않는다.
            (["소고기로"], "소불고기", ""),
            (["돼지고기 요리"], "돼지불고기", ""),
            (["볶음 요리"], "볶음밥", ""),
            (["매운 거 먹고 싶어"], "매운탕", ""),
            (["감자 들어간 거"], "감자탕", ""),
        ]
        for messages, model, want in cases:
            with self.subTest(messages=messages), patch.object(llm, "generate", return_value={"query": model}):
                profile, _ = llm.conditions({"messages": messages, "profile": {}}, ["분식"], foods)
                self.assertEqual(profile.query, want)

    def test_story_roles_and_negations(self):
        foods = {"치킨": {"치킨"}, "피자": {"피자"}, "라면": {"분식"}, "회": {"해산물"}, "초밥": {"일식"},
                 "전": {"전·부침"}, "죽": {"죽"}, "순대": {"분식"}, "국밥": {"국밥·해장국"}, "삼겹살": {"고기"},
                 "샐러드": {"샐러드·건강식"}, "떡볶이": {"분식"}, "햄버거": {"버거·샌드위치"}}
        categories = ["치킨", "피자", "분식", "해산물", "일식", "전·부침", "죽", "국밥·해장국", "고기", "샐러드·건강식"]
        cases = [
            (["점심에 치킨 먹어서 저녁은 다른 거 먹고 싶어"], {"query": "치킨"}, {"query": "", "avoid_menus": "치킨"}),
            (["피자랑 치킨만 빼고 아무거나"], {}, {"query": "", "avoid_menus": "피자,치킨"}),
            (["국밥 중에 순대 들어간 건 싫어"], {}, {"query": "국밥", "avoid_menus": "순대"}),
            (["너라면 뭐 먹을 거야?"], {}, {"query": ""}),
            (["치킨이라면 좋아"], {}, {"query": "치킨"}),
            (["출근 전에 아침으로 간단히"], {"query": "간단한 전"}, {"query": ""}),
            (["배고파 죽을 것 같아"], {}, {"query": ""}),
            (["회는 좋은데 초밥은 별로"], {}, {"query": "회", "avoid_menus": "초밥"}),
            (["치킨 먹을래", "아니다 어제 먹었다"], {}, {"query": "", "avoid_menus": "치킨"}),
            (["고기 먹자", "삼겹살은 어제 먹었어"], {}, {"meal_category": "고기", "avoid_menus": "삼겹살"}),
            (["점심 때 샐러드 먹었더니 배고파 죽겠어 저녁은 고기"], {"meal_category": "고기"},
             {"meal_category": "고기", "avoid_menus": "샐러드"}),
            (["친구가 매운 거 하나도 못 먹어"], {}, {"spice": "mild"}),
            (["매운 거랑 느끼한 거 둘 다 싫어"], {}, {"spice": "mild", "taste": "-rich"}),
            (["의사가 맵고 짠 거 먹지 말래"], {}, {"spice": "mild", "taste": "-salty"}),
            (["매운 거 먹고 싶은데 느끼한 건 싫어"], {}, {"spice": "hot", "taste": "-rich"}),
            (["맵찔이라 신라면도 매워"], {}, {"spice": "mild"}),
            (["국물은 있었으면 좋겠는데 맵지는 않게"], {}, {"spice": "mild", "soup": "yes"}),
            (["감기 걸려서 목이 칼칼해"], {}, {"spice": ""}),
            (["속이 쓰려서 자극적인 거 못 먹겠어"], {}, {"spice": "mild", "taste": "plain"}),
            (["추워서 몸 좀 녹이고 싶다"], {}, {"soup": "yes"}),
            (["살 빼는 중인데 배는 고파"], {}, {"goal": "light"}),
            (["다이어트는 내일부터 오늘은 맘껏 먹는다"], {}, {"goal": "balance"}),
            (["적당히 든든한 거 추천해줘"], {"query": "적당히 든든한"}, {"query": ""}),
            (["겉바속촉 먹고 싶다"], {}, {"taste": "crispy"}),
            (["고수 극혐"], {}, {"exclude": "고수"}),
            (["채식 중이야"], {}, {"diet": "vegetarian"}),
            (["고기 달걀 우유 전부 빼는 완전 채식 중이야"], {}, {"diet": "vegan", "meal_category": ""}),
            (["떡뽁이 먹고싶다"], {"query": "떡뽁이"}, {"query": "떡볶이"}),
            (["burger 추천"], {"query": "burger"}, {"query": "햄버거"}),
            (["속이 쓰려서 자극적인 거 못 먹겠어"], {"exclude": "자극적인"}, {"exclude": ""}),
        ]
        for messages, model, want in cases:
            with self.subTest(messages=messages), patch.object(llm, "generate", return_value=model):
                profile, _ = llm.conditions({"messages": messages, "profile": {}}, categories, foods)
                self.assertEqual({key: getattr(profile, key) for key in want}, want)

    def test_condition_stages(self):
        foods = {"짜장면": {"중식"}, "칼국수": {"면"}}
        story = llm.read_story(["어제 짜장면 먹었고, 오늘은 칼국수 국물로"], foods)
        self.assertNotIn("짜장면", story.current)
        self.assertIn("칼국수", story.current)
        self.assertNotIn("칼국수", story.cue_text)  # 메뉴 이름 속 글자는 단서가 아니다
        values = {"goal": "balance", "spice": "", "soup": ""}
        result = {"spice": "hot", "soup": "no"}
        llm.ground_cues(values, result, story)  # 문장에 매운 단서가 없고 '국물'은 있다
        self.assertEqual((values["spice"], values["soup"]), ("", "yes"))
        with self.assertRaises(llm.Unavailable):
            llm.ground_cues(dict(values), {"query": ["칼국수"]}, story)
        values = {"meal_category": ""}
        llm.resolve_category(values, {}, llm.read_story(["해장이 필요해"], {}), ["국밥·해장국"])
        self.assertEqual(values["meal_category"], "국밥·해장국")
        values = {"allergy": "땅콩", "exclude": ""}
        llm.resolve_ingredients(values, {"exclude": "오이,땅콩"}, llm.read_story(["오이 빼 줘"], {}))
        self.assertEqual((values["allergy"], values["exclude"]), ("땅콩", "오이"))
        values = {"query": "", "meal_category": "중식", "avoid_menus": ""}
        llm.resolve_menus(values, {"query": "칼국수"}, story, ["중식", "면"])
        self.assertEqual((values["query"], values["avoid_menus"], values["meal_category"]), ("칼국수", "짜장면", ""))
        values = {"diet": "general", "district": ""}
        llm.apply_explicit(values, ["비건이야", "영통구에서"])
        self.assertEqual((values["diet"], values["district"]), ("vegan", "영통구"))
        with self.assertRaises(ValueError):
            llm.read_history({"messages": []})

    def test_reply_without_understood_conditions_asks_back(self):
        family = {"name": "피자", "best": {"reasons": []}}
        with patch.object(llm, "generate") as generate:
            answer = llm.reply({"families": [family]}, understood=False)
            generate.assert_not_called()
        self.assertIn("조건을 찾지 못해", answer)
        self.assertNotIn("조건에 맞춰", answer)
        payload = {"messages": ["안녕"], "profile": {"spice": "mild"}}
        with patch.object(llm, "generate", return_value={}):
            profile, _ = llm.conditions(payload, [])
        self.assertFalse(llm.understood(profile, payload, []))
        with patch.object(llm, "generate", return_value={}):
            profile, _ = llm.conditions({**payload, "messages": ["국물 있는 거"]}, [])
        self.assertTrue(llm.understood(profile, payload, []))

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
            return {"menu_name": name, "store_name": f"{name}집", "district": "권선구", "reviews": reviews,
                    "evidence": {"menu_stores": 12, "menu_share": 0.05}}
        result = {"district": "권선구", "citywide": False, "stores": [row(f"메뉴{i}", 7 + i) for i in range(12)]}
        with patch.object(llm, "generate", side_effect=llm.Unavailable):
            llm.store_comments(result)
        self.assertNotIn("comment", result["stores"][10])
        self.assertIn("가장 많이", result["stores"][9]["comment"])  # 리뷰가 가장 많은 가게
        self.assertTrue(all("comment" in r for r in result["stores"][:10]))
        # 리뷰 수는 바로 위 줄에 있으므로 추천이유에서 다시 말하지 않는다.
        self.assertFalse(any("건" in r["comment"] for r in result["stores"][:10]))
        self.assertEqual(result["stores"][0]["comment"], "권선구에서 이 메뉴를 파는 가게 12곳 중 하나예요.")
        self.assertEqual(result["stores"][0]["comment_mood"], "")

    def test_mood_words_are_capped_within_one_answer(self):
        stores = [{"menu_name": f"메뉴{i}", "store_name": f"가게{i}", "district": "권선구", "reviews": 20} for i in range(4)]
        written = {"0": "따뜻한 저녁 한 그릇으로", "1": "따뜻한 점심 가볍게", "2": "따뜻한 주말 든든하게", "3": "비 오는 날 뜨끈하게"}
        result = {"district": "권선구", "stores": [dict(s) for s in stores]}
        with patch.object(llm, "generate", return_value=written):
            llm.store_comments(result)
        # '따뜻'은 두 번까지만. 세 번째 한마디는 사실 문장만 남긴다.
        self.assertEqual([bool(s["comment_mood"]) for s in result["stores"]], [True, True, False, True])

    def test_fact_sentences_use_db_evidence_and_rotate(self):
        base = {"menu_name": "아구찜", "store_name": "아구집", "district": "권선구", "reviews": 40}
        rich = {**base, "evidence": {"menu_stores": 8, "menu_share": 0.234, "repeat_ratio": 0.49,
                                     "positive_ratio": 0.95, "quantity": "많음"}}
        first = llm.fact_sentences(rich, 100, "권선구", 0)
        self.assertEqual(first, "권선구에서 이 메뉴 리뷰의 23%가 이 가게에 모였어요. 이 가게 단골 주문 비율은 49%예요.")
        self.assertIn("95%가 만족한 리뷰", llm.fact_sentences(rich, 100, "권선구", 1))  # 다음 줄은 다른 근거
        self.assertIn("양이 넉넉", llm.fact_sentences(rich, 100, "권선구", 2))
        low = {**base, "evidence": {"menu_stores": 8, "menu_share": 0.05, "repeat_ratio": 0.2,
                                    "positive_ratio": 0.6, "quantity": "적음"}}
        self.assertEqual(llm.fact_sentences(low, 100, "수원시", 0), "수원시에서 이 메뉴를 파는 가게 8곳 중 하나예요.")
        self.assertEqual(llm.fact_sentences({**base, "reviews": 3}, 100, "권선구"),
                         "아직 리뷰가 적어 새로 찾아볼 만한 가게예요.")
        self.assertEqual(llm.fact_sentences(base, 100, "권선구"), "이 메뉴를 파는 가게예요.")

    def test_store_reasons_keep_only_safe_ai_text(self):
        stores = [{"menu_name": "불향가득제육", "store_name": "제육대가", "district": "영통구", "reviews": 120},
                  {"menu_name": "매운 제육", "store_name": "한끼식당", "district": "영통구", "reviews": 30},
                  {"menu_name": "김치찌개", "store_name": "찌개집", "district": "영통구", "reviews": 20}]
        written = {"0": "쌀쌀한 저녁 한 접시로", "1": "제육대가보다 저렴한 집", "2": "쌀쌀한 저녁 한 접시로"}
        result = {"district": "영통구", "citywide": False, "stores": [dict(s) for s in stores]}
        with patch.object(llm, "generate", return_value=written) as generate:
            llm.store_comments(result)
        self.assertEqual(generate.call_args.kwargs["temperature"], 0.8)
        sent = generate.call_args.args[1]["menus"]
        # 숫자·가게 이름은 보내지 않고, 메뉴마다 다른 상황(scene)을 하나씩 건넨다.
        self.assertEqual({k: v for k, v in sent[0].items() if k != "scene"}, {"menu": "불향가득제육", "district": "영통구"})
        self.assertEqual(len({item["scene"] for item in sent}), len(sent))
        self.assertEqual(result["stores"][0]["comment_mood"], "쌀쌀한 저녁 한 접시로")
        self.assertTrue(result["stores"][0]["comment"].endswith("쌀쌀한 저녁 한 접시로"))
        self.assertEqual(result["stores"][1]["comment_mood"], "")  # 금지어·다른 가게 이름
        self.assertEqual(result["stores"][2]["comment_mood"], "")  # 앞 줄과 같은 한마디
        ok = stores[0]
        self.assertTrue(llm.safe_mood("주말 늦은 점심에 가볍게", ok, set(), set()))
        for bad in ("리뷰 120건이 쌓인 한 접시", "120명이 고른 저녁", "퇴근길 지친 저녁에", "이 동네 최고의 맛집이에요",
                    "간장의 깊이가 매력적인", "늦은 밤 푸근한 밥 한 쟁抓好", "새로 오픈한 매장", "단골이 많은 집", "짧다"):
            with self.subTest(bad=bad):
                self.assertFalse(llm.safe_mood(bad, ok, set(), set()))
        plain = {"menu_name": "콤비네이션피자", "store_name": "피자집", "district": "권선구", "reviews": 9}
        self.assertFalse(llm.safe_mood("매콤한 기분 전환이 필요한 날", plain, set(), set()))
        self.assertTrue(llm.safe_mood("매콤한 기분 전환이 필요한 날", stores[1], set(), set()))
        few = {"district": "팔달구", "citywide": False,
               "stores": [{"menu_name": "짬뽕", "store_name": "만빈원", "district": "팔달구", "reviews": 2}]}
        with patch.object(llm, "generate", side_effect=llm.Unavailable):
            llm.store_comments(few)
        self.assertEqual(few["stores"][0]["comment"], "아직 리뷰가 적어 새로 찾아볼 만한 가게예요.")

    def test_empty_results_do_not_prompt_model(self):
        with patch.object(llm, "generate") as generate:
            self.assertIn("찾지 못했", llm.reply({"families": []}))
            generate.assert_not_called()

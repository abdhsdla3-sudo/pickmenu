"""가게 추천이유 AI 한마디(`comment_mood`)를 실제 모델로 측정한다.

참고자료 DB에서 리뷰 5건 이상인 매장 메뉴를 음식(컬럼)별로 묶어 상위 10곳씩 `llm.store_comments`에 넣고,
최종 통과율·고유 문장 비율·자주 쓴 낱말·'따뜻' 비율·예시 복사 수를 출력한다. 원본 리뷰는 읽지 않는다.

실행(서버, 모델 연결 필요):
    PICKMENU_LLM_URL=http://127.0.0.1:11435 PYTHONPATH=. .venv/bin/python eval/store_mood_eval.py --seed 7 --batches 15
"""

import argparse
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm  # noqa: E402
import menu_reco  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--batches", type=int, default=15)
    args = parser.parse_args()
    import psycopg

    with psycopg.connect(menu_reco.dsn(), connect_timeout=5) as db:
        db.read_only = True
        rows = db.execute("SELECT column_name, display_name, store_name, district, review_count "
                          "FROM pickmenu_data.current_menu_scores WHERE review_count >= 5").fetchall()
    by_column: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for column, menu, store, district, reviews in rows:
        by_column[column].append({"menu_name": menu, "store_name": store, "district": district, "reviews": reviews})
    columns = sorted(name for name, items in by_column.items() if len(items) >= 4)
    random.Random(args.seed).shuffle(columns)
    moods: list[str] = []
    total = 0
    for column in columns[:args.batches]:
        stores = sorted(by_column[column], key=lambda item: -int(item["reviews"]))[:10]
        result: dict[str, Any] = {"district": "", "stores": [dict(store) for store in stores]}
        llm.store_comments(result)
        total += len(result["stores"])
        moods.extend(row["comment_mood"] for row in result["stores"] if row["comment_mood"])
    words = Counter(word for mood in moods for word in re.findall(r"[가-힣]{2,}", mood))
    print(f"최종 통과 {len(moods)}/{total} ({round(100 * len(moods) / max(total, 1))}%) · 고유 {len(set(moods))}")
    print(f"'따뜻' 비율 {round(100 * sum('따뜻' in mood for mood in moods) / max(len(moods), 1))}% · "
          f"예시 복사 {sum(mood in llm.MOOD_EXAMPLES for mood in moods)}")
    print("자주 쓴 낱말:", words.most_common(10))
    print("예:", moods[:20])


if __name__ == "__main__":
    main()

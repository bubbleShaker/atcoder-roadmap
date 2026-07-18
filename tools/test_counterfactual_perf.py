"""counterfactual_perf の純粋関数のテスト。ネットワークは使わない。

実行: python tools/test_counterfactual_perf.py
"""

from __future__ import annotations

import sys

from counterfactual_perf import Entry, Standings, build_standings, perf_at_place

POINTS = {"x_a": 100, "x_b": 200, "x_c": 300}
START = 1000


def sub(user: str, problem: str, sec: int, result: str, sid: int) -> dict:
    return {
        "id": sid,
        "user_id": user,
        "problem_id": problem,
        "result": result,
        "epoch_second": START + sec,
        "contest_id": "x",
    }


def test_score_is_sum_of_solved_points() -> None:
    st = build_standings(
        [sub("u", "x_a", 60, "AC", 1), sub("u", "x_b", 120, "AC", 2)], POINTS, START
    )
    assert st["u"].score == 300
    assert st["u"].elapsed == 120  # 最後の AC の時刻


def test_penalty_only_for_solved_problems() -> None:
    """AC した問題の誤答だけが 5分ペナルティになる。未 AC の問題の誤答は数えない。"""
    st = build_standings(
        [
            sub("u", "x_a", 60, "WA", 1),  # 後で AC する → ペナルティ
            sub("u", "x_a", 90, "AC", 2),
            sub("u", "x_c", 100, "WA", 3),  # 最後まで AC しない → ペナルティにならない
            sub("u", "x_c", 200, "TLE", 4),
        ],
        POINTS,
        START,
    )
    assert st["u"].score == 100
    assert st["u"].elapsed == 90 + 300  # AC 90秒 + 誤答1回


def test_compile_error_is_not_a_penalty() -> None:
    st = build_standings(
        [sub("u", "x_a", 10, "CE", 1), sub("u", "x_a", 60, "AC", 2)], POINTS, START
    )
    assert st["u"].elapsed == 60  # CE はペナルティに数えない


def test_submissions_after_ac_are_ignored() -> None:
    st = build_standings(
        [
            sub("u", "x_a", 60, "AC", 1),
            sub("u", "x_a", 120, "WA", 2),  # AC 後の提出は順位に影響しない
        ],
        POINTS,
        START,
    )
    assert st["u"].score == 100
    assert st["u"].elapsed == 60


def test_zero_solved_is_zero_elapsed() -> None:
    st = build_standings([sub("u", "x_a", 60, "WA", 1)], POINTS, START)
    assert st["u"].score == 0
    assert st["u"].elapsed == 0


def test_bases_of_missing_distinct_places() -> None:
    """公式順位が 2位・5位 の欠損者は、復元側では 2番目・4番目の位置に挿さる。

    5位の欠損者の上には、もう1人の欠損者(2位)がいるので、復元済み参加者は 5-1-1 = 3人。
    """
    assert Standings.bases_of_missing([2, 5]) == [2, 4]


def test_bases_of_missing_tied_places() -> None:
    """欠損者どうしが同着のとき、互いを「上」に数えてはいけない。

    2位に欠損者が2人同着 → どちらの上にも復元済み参加者は1人しかいない → 両方とも 2 の位置。
    （ここを `P - i + 1` で計算すると [1, 2] になり、1人ぶん順位がずれる）
    """
    assert Standings.bases_of_missing([2, 2]) == [2, 2]


def test_place_range_without_missing_is_exact() -> None:
    keys = sorted([(-300, 100), (-200, 50), (-200, 80)])
    st = Standings(keys=keys, missing_bases=[])
    assert st.place_range((-300, 100)) == (1, 1)
    assert st.place_range((-200, 50)) == (2, 2)
    assert st.place_range((-200, 80)) == (3, 3)
    assert st.place_range((-200, 80)) == (3, 3)
    assert st.place_range((-100, 10)) == (4, 4)  # 誰よりも下


def test_place_range_ties_share_the_same_place() -> None:
    keys = sorted([(-300, 100), (-300, 100), (-200, 50)])
    st = Standings(keys=keys, missing_bases=[])
    assert st.place_range((-300, 100)) == (1, 1)  # 同着は同順位
    assert st.place_range((-200, 50)) == (3, 3)  # 同着2人の下は3位


def test_place_range_shifts_by_missing_above() -> None:
    """自分より上にいる欠損者の数だけ、公式順位は下がる。"""
    keys = sorted([(-300, 100), (-200, 50)])
    st = Standings(keys=keys, missing_bases=[1])  # 復元側の1番目の位置に欠損者が1人
    assert st.place_range((-200, 50)) == (3, 3)  # base 2 + 上の欠損者 1


def test_place_range_is_ambiguous_only_when_a_missing_user_may_tie() -> None:
    """欠損者の挿入位置が自分と重なるときだけ、同着か1つ上かが決まらず幅1になる。

    reviewer が出した最小反例そのもの:
      復元済み R1(1位相当) と R2、欠損 M1・M2 が同着2位、公式は R1=1位 / M=2位 / R2=4位。
    """
    keys = sorted([(-300, 10), (-100, 10)])  # R1, R2
    st = Standings(keys=keys, missing_bases=Standings.bases_of_missing([2, 2]))
    assert st.missing_bases == [2, 2]

    assert st.place_range((-300, 10)) == (1, 1)  # R1: 欠損者は自分より下 → 一意に決まる

    # R2 は欠損者2人と挿入位置が重なる。欠損者の得点・時間は分からないので、
    # 「2人とも自分と同着(2位)」から「2人とも自分より上(4位)」までしか絞れない。
    # 公式の 4位 はこの範囲に含まれる（外れていたら復元ロジックが誤り）。
    assert st.place_range((-100, 10)) == (2, 4)

    # 重なる欠損者が1人なら幅も1になる
    st2 = Standings(keys=keys, missing_bases=[2])
    assert st2.place_range((-100, 10)) == (2, 3)


def test_perf_at_place_interpolates_between_measured_points() -> None:
    table = [(1, 2000), (11, 1000)]
    assert perf_at_place(1, table) == 2000
    assert perf_at_place(11, table) == 1000
    assert perf_at_place(6, table) == 1500  # 実測点のあいだは線形補間


def test_perf_at_place_clamps_outside_the_measured_range() -> None:
    table = [(10, 2000), (20, 1000)]
    assert perf_at_place(1, table) == 2000  # 表より上は最上位のパフォで頭打ち
    assert perf_at_place(99, table) == 1000  # 表より下は最下位のパフォで頭打ち


def test_entry_key_orders_by_score_then_time() -> None:
    high = Entry(score=600, elapsed=5000)
    low_fast = Entry(score=300, elapsed=100)
    same_slow = Entry(score=600, elapsed=6000)
    assert high.key() < low_fast.key()  # 得点が高い方が上
    assert high.key() < same_slow.key()  # 同点なら速い方が上


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # AssertionError 以外で落ちても残りを走らせる
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

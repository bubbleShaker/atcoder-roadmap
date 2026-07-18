"""score_bands の純粋関数のテスト。ネットワークは使わない。

実行: python tools/test_score_bands.py

このスクリプトの出力は「ABC467 の A は AC していた」という事実認定の根拠に使ったので、
集計と突合のロジックはここで固定しておく。
"""

from __future__ import annotations

import sys

from score_bands import Band, bands_matching, build_score_bands, parse_place_spec


def test_empty_input_gives_no_bands() -> None:
    assert build_score_bands([]) == []
    assert bands_matching([], place=1, total=100, other_total=100) == []


def test_bands_are_sorted_by_score_desc() -> None:
    bands = build_score_bands([(100, 900), (300, 100), (200, 500)])
    assert [b.score for b in bands] == [300, 200, 100]


def test_band_collects_places_of_same_score() -> None:
    bands = build_score_bands([(300, 50), (300, 10), (300, 30)])
    assert len(bands) == 1
    band = bands[0]
    assert (band.count, band.best, band.median, band.worst) == (3, 10, 30, 50)


def test_median_of_even_count_takes_upper_middle() -> None:
    """人数が偶数のときは中央2つの後ろ側。既存の挙動を固定する。"""
    band = build_score_bands([(300, 10), (300, 20), (300, 30), (300, 40)])[0]
    assert band.median == 30


def test_pct_range_uses_total_participants() -> None:
    band = Band(score=300, count=10, best=25, median=50, worst=75)
    assert band.pct_range(100) == (25.0, 75.0)
    assert band.median_pct(100) == 50.0


def test_matching_band_contains_the_percentile() -> None:
    """逆算対象の上位%を含む帯だけが返る。参加者数が違っても%で突き合わせる。"""
    bands = build_score_bands([(300, 60), (300, 80), (200, 90), (200, 95)])
    # 対応表は100人、逆算対象は200人中140位 = 上位70% → 300点帯(60〜80%)に入る
    matched = bands_matching(bands, place=140, total=100, other_total=200)
    assert [b.score for b in matched] == [300]


def test_matching_is_inclusive_at_both_ends() -> None:
    bands = build_score_bands([(300, 60), (300, 80)])
    assert bands_matching(bands, place=60, total=100, other_total=100)  # 最良端
    assert bands_matching(bands, place=80, total=100, other_total=100)  # 最悪端
    assert not bands_matching(bands, place=81, total=100, other_total=100)


def test_matching_includes_small_bands() -> None:
    """人数の少ない帯も突合対象に残す（表示からは省くが、判定から落とすと誤読を生む）。"""
    bands = build_score_bands([(300, 10), (300, 20), (300, 30), (300, 40), (300, 50), (200, 70)])
    small = [b for b in bands if b.score == 200][0]
    assert small.count == 1
    matched = bands_matching(bands, place=70, total=100, other_total=100)
    assert [b.score for b in matched] == [200]


def test_abc466_bands_reject_200_points() -> None:
    """実際に使った判定の再現: ABC467 の上位55.2%は 300点帯に入り、200点帯には入らない。

    数値は ABC466 の復元結果（300点=7251〜10289位、200点=10290〜10312位 / 13301人）。
    """
    bands = [
        Band(score=300, count=3036, best=7251, median=8772, worst=10289),
        Band(score=200, count=23, best=10290, median=10301, worst=10312),
    ]
    matched = bands_matching(bands, place=7212, total=13301, other_total=13076)
    assert [b.score for b in matched] == [300]


def test_parse_place_spec() -> None:
    assert parse_place_spec("7212/13076") == (7212, 13076)


def test_parse_place_spec_rejects_bad_input() -> None:
    for bad in ["7212", "7212/13076/1", "a/b", "0/100", "100/0", "200/100", ""]:
        try:
            parse_place_spec(bad)
        except ValueError:
            continue
        raise AssertionError(f"ValueError にならなかった: {bad!r}")


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

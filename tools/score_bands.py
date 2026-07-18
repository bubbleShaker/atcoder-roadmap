"""復元した順位表から「得点帯 → 公式順位帯」の対応を出す。

用途: **公式順位しか分からない回について、得点を逆算する**。

AtCoder Problems 側の提出データが欠けていると「何点だったか」が分からなくなる
（ABC467 では A の提出が丸ごと欠けていた）。一方で公式の順位・パフォは必ず取れる。
そこで、**提出データが揃っている別の回**で「得点 → 順位」の対応表を作り、
そこに公式順位を当てて得点を絞り込む。

例: ABC467 の Coji は 7212位 / 13076人（上位55.2%）。
    ABC466 の対応表では 200点は上位77.4%（23人しかいない）、300点は上位54.5〜77.4%。
    → 200点ではありえず、300点帯と整合する。

**注意**: 配点構成が違う回の対応表を当てるのは厳密な同値ではない。
帯が大きく離れているとき（上の例では約22ポイント）の排除にのみ使うこと。

使い方:
    python tools/score_bands.py abc466 --start "2026-07-11 21:00" --minutes 100 \
        --user Coji --compare 7212/13076
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# counterfactual_perf は同じ tools/ にあるスクリプト。append にしているのは、
# insert(0) だと tools/ 配下のファイルが標準ライブラリを食う恐れがあるため。
sys.path.append(str(Path(__file__).resolve().parent))

from counterfactual_perf import (  # noqa: E402
    CONTEST_ID_RE,
    JST,
    build_perf_table,
    build_verified_standings,
    fetch_points,
    fetch_submissions,
    perf_at_place,
)

MIN_BAND_SIZE = 5
"""表に載せる帯の最小人数。

これ未満の帯は、順位の幅が「その得点を取った人の実力分布」ではなく
たまたま居合わせた数人の位置でしかないため、表からは省く。
ただし **突合（--compare）からは省かない**。小さい帯が一致した場合に
「一致する帯は無かった」と誤読させないため。
"""


@dataclass(frozen=True)
class Band:
    """同じ得点だった参加者の集まりと、その順位の広がり。"""

    score: int
    count: int
    best: int  # 最上位の順位
    median: int
    worst: int  # 最下位の順位

    def pct_range(self, total: int) -> tuple[float, float]:
        """この帯が占める上位%の範囲（最良, 最悪）。"""
        return self.best / total * 100, self.worst / total * 100

    def median_pct(self, total: int) -> float:
        return self.median / total * 100


def build_score_bands(scored_places: list[tuple[int, int]]) -> list[Band]:
    """(得点, 順位) の列から得点帯を作る。得点の高い順に返す。

    人数によるフィルタはここでは行わない（表示側の判断なので分離する）。
    """
    by_score: dict[int, list[int]] = {}
    for score, place in scored_places:
        by_score.setdefault(score, []).append(place)

    bands = []
    for score, places in by_score.items():
        places.sort()
        bands.append(
            Band(
                score=score,
                count=len(places),
                best=places[0],
                median=places[len(places) // 2],
                worst=places[-1],
            )
        )
    return sorted(bands, key=lambda b: -b.score)


def bands_matching(bands: list[Band], place: int, total: int, other_total: int) -> list[Band]:
    """公式順位 place/other_total の上位%を含む帯を返す。

    対応表と逆算対象で参加者数が違うので、順位そのものではなく上位%で突き合わせる。
    """
    pct = place / other_total * 100
    matched = []
    for band in bands:
        lo, hi = band.pct_range(total)
        if lo <= pct <= hi:
            matched.append(band)
    return matched


def parse_place_spec(text: str) -> tuple[int, int]:
    """'7212/13076' を (順位, 参加者数) にする。不正な入力は ValueError。"""
    parts = text.split("/")
    if len(parts) != 2:
        raise ValueError(f"'順位/参加者数' の形式で指定する: {text!r}")
    try:
        place, total = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"順位と参加者数は整数で指定する: {text!r}") from None
    if place < 1 or total < 1:
        raise ValueError(f"順位と参加者数は1以上: {text!r}")
    if place > total:
        raise ValueError(f"順位が参加者数を超えている: {text!r}")
    return place, total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("contest", help="対応表を作るコンテストID（提出データが揃っている回）")
    ap.add_argument("--start", required=True, help="開始時刻 JST（例: '2026-07-11 21:00'）")
    ap.add_argument("--minutes", required=True, type=int, help="コンテスト時間（分）")
    ap.add_argument("--user", help="この人の位置も併記する")
    ap.add_argument(
        "--compare",
        metavar="PLACE/TOTAL",
        help="逆算したい回の公式順位（例: '7212/13076'）。同じ上位%%の得点帯と突き合わせる",
    )
    args = ap.parse_args()

    if not CONTEST_ID_RE.match(args.contest):
        ap.error(f"コンテストIDの形式が不正: {args.contest!r}")
    try:
        compare = parse_place_spec(args.compare) if args.compare else None
    except ValueError as e:
        ap.error(str(e))

    start = int(
        datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=JST).timestamp()
    )
    points = fetch_points(args.contest)
    submissions = fetch_submissions(args.contest, start, start + args.minutes * 60)
    standings, entries, results = build_verified_standings(
        args.contest, submissions, points, start
    )
    table = build_perf_table(results)

    # 同着最下位があるため max(Place) は参加者数より小さい。分母は参加者数を使う。
    total = len(results)

    # place_range は欠損者との同着判定が付かないとき幅1を返す。最良端で代表させるので
    # 上位%は最大1位ぶん良く出るが、帯の比較には影響しない大きさ。
    bands = build_score_bands(
        [(e.score, standings.place_range(e.key())[0]) for e in entries.values()]
    )

    print(f"{args.contest}: 参加者 {total}人")
    print(f"{'得点':>6} {'人数':>6} {'最良':>7} {'中央':>7} {'最悪':>7} {'中央の上位%':>12} {'中央のperf':>10}")
    hidden = [b for b in bands if b.count < MIN_BAND_SIZE]
    for band in bands:
        if band.count < MIN_BAND_SIZE:
            continue
        print(
            f"{band.score:>6} {band.count:>6} {band.best:>7} {band.median:>7} {band.worst:>7} "
            f"{band.median_pct(total):>11.1f}% {perf_at_place(band.median, table):>10}"
        )
    if hidden:
        print(f"（{MIN_BAND_SIZE}人未満のため非表示: {len(hidden)}帯 / "
              f"{sum(b.count for b in hidden)}人。突合には含めている）")

    # 提出が1件も無い参加者は帯に入らないが分母には入る。低得点帯ほど人数が過小に出る。
    covered = sum(b.count for b in bands)
    print(f"（帯に載っているのは提出データがある {covered}人。残り {total - covered}人は提出データなし）")

    if args.user:
        if args.user not in entries:
            print(f"\n警告: {args.user} は復元した順位表に居ない（提出データが無いか、ユーザー名が違う）")
        else:
            entry = entries[args.user]
            place = standings.place_range(entry.key())[0]
            print(f"\n{args.user}: {entry.score}点 → {place}位（上位 {place / total * 100:.1f}%）")

    if compare:
        place, other_total = compare
        print(f"\n逆算対象: {place}位 / {other_total}人（上位 {place / other_total * 100:.1f}%）")
        matched = bands_matching(bands, place, total, other_total)
        if not matched:
            print("  → 一致する帯なし")
        for band in matched:
            lo, hi = band.pct_range(total)
            note = "" if band.count >= MIN_BAND_SIZE else f"（{band.count}人のみ・表では非表示）"
            print(f"  → {band.score}点の帯（上位 {lo:.1f}〜{hi:.1f}%）と整合{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

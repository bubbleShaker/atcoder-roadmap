"""AtCoder の順位表を公開データから復元し、「もし通していたらパフォいくつだったか」を実測ベースで求める。

背景:
    `https://atcoder.jp/contests/<c>/standings/json` はログイン必須のため取得できない。
    一方で以下は公開されている:
      - `https://atcoder.jp/contests/<c>/results/json`   … 全参加者の Place / Performance
      - `https://kenkoooo.com/atcoder/atcoder-api/v3/from/<epoch>` … 全提出のストリーム
    そこで提出ストリームから順位表を復元し、results/json の Place→Performance で
    仮想順位をパフォに変換する。

    復元が正しいかは「全参加者の復元順位が results/json の Place と一致するか」で検証する。
    1人でも外れたら結果を信用してはならないので、その場合は中止する。

使い方:
    python tools/counterfactual_perf.py abc466 --start "2026-07-11 21:00" --minutes 100 \
        --user Coji --scenario 600:30 --scenario 1000:95
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
CACHE = Path(__file__).resolve().parent / ".cache"
USER_AGENT = "atcoder-roadmap/1.0 (personal contest analysis)"

# kenkoooo の API は「1秒に1リクエストまで」を明示的に要請している。AtCoder 側もクロール間隔を求めている。
REQUEST_INTERVAL_SEC = 1.0

# コンテストIDはそのまま URL とキャッシュのファイル名に入るので、形式を検証する。
CONTEST_ID_RE = re.compile(r"^[0-9a-z_\-]+$")

# AtCoder の順位規則: 誤答1回につき5分のペナルティ（AC した問題の誤答のみ数える）。
PENALTY_SEC = 300
# コンパイルエラーはペナルティに数えない。
NON_PENALTY_RESULTS = frozenset({"AC", "CE"})


_last_request_at = 0.0


def fetch(url: str, cache_name: str) -> bytes:
    """URL を取得する。取得済みならキャッシュを返す（API を無駄に叩かないため）。

    実際にネットワークを叩くときだけ間隔を空ける。
    kenkoooo は「1秒に1リクエストまで」を明示的に要請しており、AtCoder もクロール間隔を空けるよう求めている。
    キャッシュヒット時に待つのは無駄なので、待つのは通信の直前に限る。
    """
    global _last_request_at
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / cache_name
    if cached.exists():
        return cached.read_bytes()

    wait = REQUEST_INTERVAL_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as res:
        body = res.read()
    # 書き込み途中で中断されても壊れたキャッシュが残らないように、別名で書いてから差し替える。
    tmp = cached.with_suffix(cached.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(cached)
    return body


def fetch_points(contest: str) -> dict[str, int]:
    """各問題の配点を問題ページから読む。

    配点は回によって違うので推測しない（ABC466 は E=450, F=500, G=600 で「よくある配点」と異なる）。
    """
    points: dict[str, int] = {}
    for letter in "abcdefgh":
        problem_id = f"{contest}_{letter}"
        try:
            html = fetch(
                f"https://atcoder.jp/contests/{contest}/tasks/{problem_id}",
                f"{problem_id}.html",
            ).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue  # 問題数は回によって違う
            raise
        m = re.search(r"配点\s*:\s*<var>(\d+)</var>", html)
        if not m:
            raise RuntimeError(f"{problem_id}: 配点をページから読めなかった")
        points[problem_id] = int(m.group(1))
    if not points:
        raise RuntimeError(f"{contest}: 問題が1つも見つからない")
    return points


def fetch_submissions(contest: str, start: int, end: int) -> list[dict]:
    """コンテスト時間中の contest の全提出を集める。

    kenkoooo の /v3/from/<epoch> は epoch_second 昇順に最大1000件返す（全コンテスト混在）。
    次ページは「最後の epoch_second」から取り直し、id で重複を除く。
    同一秒に1ページぶんを超える提出があると前進しなくなるため、その場合だけ1秒進める
    （ABC のピークでも毎秒15件程度なので通常は起きないが、無限ループを避けるための保険）。
    """
    seen: set[int] = set()
    subs: list[dict] = []
    cursor = start
    while cursor <= end:
        page = json.loads(
            fetch(
                f"https://kenkoooo.com/atcoder/atcoder-api/v3/from/{cursor}",
                f"{contest}_from_{cursor}.json",
            )
        )
        if not page:
            break

        fresh = [s for s in page if s["id"] not in seen]
        seen.update(s["id"] for s in page)
        subs.extend(
            s
            for s in fresh
            if s["contest_id"] == contest and start <= s["epoch_second"] <= end
        )

        last = max(s["epoch_second"] for s in page)
        cursor = last if fresh else last + 1
    return subs


@dataclass
class Entry:
    """1参加者の順位表上の成績。"""

    score: int = 0
    elapsed: int = 0  # 最後の AC までの秒数 + ペナルティ秒
    solved: list[str] = field(default_factory=list)

    def key(self) -> tuple[int, int]:
        """順位のソートキー。得点が高いほど上、同点なら時間が短いほど上。"""
        return (-self.score, self.elapsed)


def build_standings(
    submissions: list[dict], points: dict[str, int], start: int
) -> dict[str, Entry]:
    """提出列から順位表を復元する。

    AtCoder の規則:
      - 得点 = AC した問題の配点合計
      - 時間 = 最後の AC までの経過時間 + 300秒 × (AC した問題での誤答数)
      - AC していない問題の誤答はペナルティに数えない
      - 一度 AC した問題へのその後の提出は無視する
    """
    # user -> problem -> {"ac": 初ACまでの秒 or None, "wa": 初AC前の誤答数}
    per_user: dict[str, dict[str, dict]] = {}

    for s in sorted(submissions, key=lambda s: (s["epoch_second"], s["id"])):
        if s["problem_id"] not in points:
            continue
        problems = per_user.setdefault(s["user_id"], {})
        st = problems.setdefault(s["problem_id"], {"ac": None, "wa": 0})
        if st["ac"] is not None:
            continue  # AC 後の提出は順位に影響しない
        if s["result"] == "AC":
            st["ac"] = s["epoch_second"] - start
        elif s["result"] not in NON_PENALTY_RESULTS:
            st["wa"] += 1

    standings: dict[str, Entry] = {}
    for user, problems in per_user.items():
        entry = Entry()
        last_ac = 0
        penalties = 0
        for problem_id, st in problems.items():
            if st["ac"] is None:
                continue  # 未 AC の問題は得点にもペナルティにも寄与しない
            entry.score += points[problem_id]
            entry.solved.append(problem_id)
            last_ac = max(last_ac, st["ac"])
            penalties += st["wa"]
        entry.elapsed = last_ac + PENALTY_SEC * penalties if entry.solved else 0
        standings[user] = entry
    return standings


@dataclass
class Standings:
    """公式順位を再現できる形にした順位表。

    提出ストリームには**提出データが丸ごと欠けている参加者**が少数いる
    （AtCoder 側には順位もパフォも付いているのに、AtCoder Problems 側に提出が無い。
    ABC466 では20人）。この人たちを無視すると、自分より上にいる欠損者の数だけ順位が良く出てしまう。

    ただし欠損者の**公式順位は results/json から分かる**ので、
    「自分より上にいる欠損者の数」を足し戻せば公式順位を再現できる。
    得点0の参加者は全員が最下位で同着になり、得点を持つ参加者の順位に影響しないので、
    復元が必要なのは「最下位より上にいる欠損者」だけでよい。
    """

    keys: list[tuple[int, int]]  # 提出から復元できた参加者のソートキー（昇順）
    missing_bases: list[int]  # 欠損者が「復元側の順位」で挿さる位置（昇順）

    @staticmethod
    def bases_of_missing(missing_places: list[int]) -> list[int]:
        """欠損者の公式順位から「復元側の順位で見た挿入位置」を求める。

        欠損者の公式順位 P は「自分より真に上にいる全員 + 1」。
        そのうち他の欠損者は「P より小さい公式順位を持つ欠損者」の数だけいる
        （同着の欠損者は真に上ではないので数えない）。
        残りが「その欠損者より上にいる復元済み参加者」の数なので、
        復元側の順位で言えば P - (P より上の欠損者数) の位置に挿さる。

        この値を A とすると、復元順位 base の参加者から見て
          A <  base … その欠損者は自分より真に上
          A == base … 同着か1つ上か判定できない（欠損者の得点・時間が分からないため）
          A >  base … その欠損者は自分より下
        となる。
        """
        places = sorted(missing_places)
        return sorted(p - bisect_left(places, p) for p in places)

    def place_range(self, key: tuple[int, int]) -> tuple[int, int]:
        """あるキーの公式順位を範囲 (最良, 最悪) で返す。同着は同順位。

        復元できた参加者の中での順位（base）に「自分より上にいる欠損者の数」を足すと公式順位になる。
        ただし**欠損者の得点・時間は分からない**ので、挿入位置がちょうど自分と重なる欠損者について
        「自分と同着」なのか「自分より1つ上」なのかを判定できない。
        そこで両方の場合を計算して範囲で返す。重ならなければ範囲は1点に潰れて一意に決まる。
        """
        base = bisect_left(self.keys, key) + 1
        best = base + bisect_left(self.missing_bases, base)  # 重なる欠損者は同着とみなす
        worst = base + bisect_right(self.missing_bases, base)  # 重なる欠損者は自分より上とみなす
        return best, worst


def build_verified_standings(
    contest: str, submissions: list[dict], points: dict[str, int], start: int
) -> tuple[Standings, dict[str, Entry], list[dict]]:
    """順位表を復元し、全参加者の公式順位が復元した範囲に収まることを確認する。

    1人でも外れたら復元ロジックが誤っているので RuntimeError を投げる（黙って進まない）。
    """
    results = json.loads(
        fetch(f"https://atcoder.jp/contests/{contest}/results/json", f"{contest}_results.json")
    )
    official = {r["UserScreenName"]: r["Place"] for r in results}
    last_place = max(official.values())  # 得点0の参加者が全員同着する順位

    raw = build_standings(submissions, points, start)
    # 公式結果に居ない提出者は参加者ではない（ABC466 では2人）。順位表から外す。
    entries = {u: e for u, e in raw.items() if u in official}

    missing_places = sorted(
        place
        for user, place in official.items()
        if user not in entries and place < last_place
    )
    standings = Standings(
        keys=sorted(e.key() for e in entries.values()),
        missing_bases=Standings.bases_of_missing(missing_places),
    )

    mismatches = []
    ambiguous = 0
    for user, entry in entries.items():
        best, worst = standings.place_range(entry.key())
        if not best <= official[user] <= worst:
            mismatches.append((user, best, worst, official[user]))
        elif best != worst:
            ambiguous += 1
    if mismatches:
        sample = ", ".join(
            f"{u}: 復元{b}〜{w}位 vs 公式{o}位" for u, b, w, o in mismatches[:5]
        )
        raise RuntimeError(
            f"公式順位が復元の範囲に収まらない参加者が {len(mismatches)}人 いる（例: {sample}）。"
            "復元ロジックが誤っているため中止する。"
        )
    print(
        f"検証: 全 {len(entries)}人の公式順位が復元の範囲に収まった"
        f"（うち {ambiguous}人は欠損者との同着判定ができず幅1、残りは一意に的中）"
    )
    return standings, entries, results


def build_perf_table(results: list[dict]) -> list[tuple[int, int]]:
    """results/json から (Place, Performance) を取る（rated 参加者のみ）。

    unrated 参加者は Performance が 0 で入っているため除外する。
    """
    table = sorted({(r["Place"], r["Performance"]) for r in results if r["IsRated"]})
    if not table:
        raise RuntimeError("rated 参加者が results/json に居ない")
    return table


def perf_at_place(place: int, table: list[tuple[int, int]]) -> int:
    """公式順位に対応するパフォを、実測の (Place, Performance) 表から引く。

    その順位ちょうどの rated 参加者が居るとは限らないので、前後の rated 参加者を順位で線形補間する。
    """
    places = [p for p, _ in table]
    i = bisect_left(places, place)
    if i == 0:
        return table[0][1]
    if i >= len(table):
        return table[-1][1]
    if places[i] == place:
        return table[i][1]
    (p_lo, perf_lo), (p_hi, perf_hi) = table[i - 1], table[i]
    ratio = (place - p_lo) / (p_hi - p_lo)
    return round(perf_lo + (perf_hi - perf_lo) * ratio)


def parse_scenario(text: str) -> tuple[int, int]:
    """'600:30' → (得点 600, 経過 30分)。経過にはペナルティぶんを含めること。"""
    score_s, minutes_s = text.split(":")
    return int(score_s), int(minutes_s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("contest", help="コンテストID（例: abc466）")
    ap.add_argument("--start", required=True, help="開始日時 JST（例: '2026-07-11 21:00'）")
    ap.add_argument("--minutes", type=int, required=True, help="コンテスト時間（分）")
    ap.add_argument("--user", required=True, help="検証と反実仮想の対象ユーザー")
    ap.add_argument(
        "--scenario",
        action="append",
        default=[],
        metavar="SCORE:MINUTES",
        help="反実仮想。'600:30' = 600点を30分（ペナルティ込み）で出した場合。複数指定可",
    )
    args = ap.parse_args()

    if not CONTEST_ID_RE.match(args.contest):
        raise ValueError(f"コンテストIDの形式が不正: {args.contest!r}")

    start = int(
        datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=JST).timestamp()
    )
    end = start + args.minutes * 60

    points = fetch_points(args.contest)
    print("配点: " + ", ".join(f"{k[-1].upper()}={v}" for k, v in points.items()))

    submissions = fetch_submissions(args.contest, start, end)
    print(f"提出: {len(submissions)}件")

    print(
        f"復元: {len({s['user_id'] for s in submissions})}人ぶんの提出を収集 "
        f"（うち提出データが欠けている参加者は公式順位から補完）"
    )
    standings, entries, results = build_verified_standings(
        args.contest, submissions, points, start
    )

    table = build_perf_table(results)

    def show(label: str, key: tuple[int, int]) -> None:
        best, worst = standings.place_range(key)
        place = f"{best}位" if best == worst else f"{best}〜{worst}位"
        perf_best, perf_worst = perf_at_place(best, table), perf_at_place(worst, table)
        perf = f"perf {perf_best}" if perf_best == perf_worst else f"perf {perf_worst}〜{perf_best}"
        print(f"  {label}  →  {place:>12s} / {perf}")

    me = entries[args.user]
    print(f"\n{args.user} の実績:")
    show(f"{me.score:5d}点 / {me.elapsed // 60:3d}分{me.elapsed % 60:02d}秒", me.key())

    if args.scenario:
        print("\n反実仮想:")
        for text in args.scenario:
            score, minutes = parse_scenario(text)
            show(f"{score:5d}点 / {minutes:3d}分00秒", (-score, minutes * 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())

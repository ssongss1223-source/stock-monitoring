import logging
from typing import Optional

import requests

import config
from models.signals import BuySignal, MarketContext, PatternLearningResult, SellSignal

logger = logging.getLogger(__name__)

_MARKET_EMOJI = {"bull": "✅", "sideways": "⚠️", "bear": "❌"}
_ACTION_EMOJI = {
    "stop_loss": "🚨",
    "full_sell": "🔴",
    "half_sell": "🟡",
    "hold": "🟢",
}
_ACTION_LABEL = {
    "stop_loss": "즉시 손절",
    "full_sell": "전량 매도",
    "half_sell": "절반 매도",
    "hold": "보유 유지",
}
_PATTERN_GRADE_EMOJI = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}
_LABEL_DISPLAY = {
    "3d_3pct": "3일+3%", "3d_5pct": "3일+5%", "3d_10pct": "3일+10%",
    "5d_3pct": "5일+3%", "5d_5pct": "5일+5%", "5d_10pct": "5일+10%",
    "10d_3pct": "10일+3%", "10d_5pct": "10일+5%", "10d_10pct": "10일+10%",
    "3d_3pct_c2": "3일+3%×2", "3d_5pct_c2": "3일+5%×2",
    "5d_3pct_c2": "5일+3%×2", "5d_5pct_c2": "5일+5%×2", "5d_10pct_c2": "5일+10%×2",
    "10d_3pct_c2": "10일+3%×2", "10d_5pct_c2": "10일+5%×2", "10d_10pct_c2": "10일+10%×2",
}
_PATTERN_GRADE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INSUFFICIENT": 3}
_GAIN_PCT     = {"3pct": 0.03, "5pct": 0.05, "10pct": 0.10}
_DAYS_MAP     = {"3d": 3, "5d": 5, "10d": 10}
_SHORT_LABELS = [
    "3d_3pct", "3d_5pct", "3d_10pct", "5d_3pct", "5d_5pct", "5d_10pct",
    "3d_3pct_c2", "3d_5pct_c2", "5d_3pct_c2", "5d_5pct_c2", "5d_10pct_c2",
]
_SWING_LABELS = [
    "10d_3pct", "10d_5pct", "10d_10pct",
    "10d_3pct_c2", "10d_5pct_c2", "10d_10pct_c2",
]


class ReportAgent:
    """텔레그램 알림 발송 에이전트."""

    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID

    async def send_collect_report(
        self,
        total: int,
        ohlcv_ok: int,
        ohlcv_fail: int,
        hourly_ok: int,
        hourly_fail: int,
        index_ok: bool,
        elapsed_sec: int,
    ) -> bool:
        message = _build_collect_message(total, ohlcv_ok, ohlcv_fail, hourly_ok, hourly_fail, index_ok, elapsed_sec)
        logger.info("ReportAgent: 수집 완료 알림 전송")
        return self._send_chunk(message)

    async def send(
        self,
        markets: dict[str, MarketContext],
        buy_signals: list[BuySignal],
        sell_signals: list[SellSignal],
        pattern_results: list[PatternLearningResult] | None = None,
        all_analyzed: list[tuple[str, str]] | None = None,
    ) -> bool:
        message = _build_message(markets, buy_signals, sell_signals, pattern_results, all_analyzed)
        logger.info("ReportAgent: 메시지 %d자 전송 시도", len(message))

        chunks = _split(message, 4096)
        success = True
        for chunk in chunks:
            if not self._send_chunk(chunk):
                success = False
        return success

    def _send_chunk(self, text: str) -> bool:
        if not self.token or not self.chat_id:
            logger.warning("텔레그램 토큰/채팅ID 미설정 — 콘솔 출력으로 대체")
            print(text)
            return True
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=10)
            if not resp.ok:
                logger.error("텔레그램 전송 실패: %s %s", resp.status_code, resp.text)
            return resp.ok
        except Exception as e:
            logger.exception("텔레그램 전송 오류: %s", e)
            return False


# ── 메시지 빌더 ───────────────────────────────────────────────────────────────

def _build_message(
    markets: dict[str, MarketContext],
    buy_signals: list[BuySignal],
    sell_signals: list[SellSignal],
    pattern_results: list[PatternLearningResult] | None = None,
    all_analyzed: list[tuple[str, str]] | None = None,
) -> str:
    parts = [_header()]
    if markets:
        parts.append(_market_section(markets))

    if sell_signals:
        parts.append(_sell_section(sell_signals))

    if buy_signals:
        pr_by_ticker = {pr.ticker: pr for pr in (pattern_results or [])}
        groups = _four_groups(buy_signals)
        parts.append(_prediction_summary_section(*groups))
        parts.append(_buy_detail_section(*groups, pr_by_ticker))
    else:
        parts.append("📭 <b>오늘 상승 예측 종목 없음</b>")

    return "\n\n".join(parts)


def _header() -> str:
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    return f"📈 <b>주식 신호 알림 — {today}</b>\n  전일 종가 기준 | KST 06:00"


def _market_section(markets: dict[str, MarketContext]) -> str:
    lines = ["🌍 <b>장세 판단</b>"]
    for name, ctx in markets.items():
        em = _MARKET_EMOJI.get(ctx.market_status, "❓")
        lines.append(
            f"  {name}: {em} {ctx.market_status.upper()} (점수 {ctx.score}점)"
        )
    return "\n".join(lines)


def _sell_section(signals: list[SellSignal]) -> str:
    lines = ["🔔 <b>보유 종목 매도 신호</b>"]
    for s in signals:
        em = _ACTION_EMOJI.get(s.action, "")
        label = _ACTION_LABEL.get(s.action, s.action)
        profit_str = f"+{s.profit_pct:.1f}%" if s.profit_pct >= 0 else f"{s.profit_pct:.1f}%"
        lines.append(
            f"\n{em} [{label}] <b>{s.name} ({s.ticker})</b>\n"
            f"  수익률: {profit_str} | 현재가: {s.current_price:,.0f}원\n"
            f"  사유: {s.reason}"
        )
    return "\n".join(lines)


def _all_stocks_section(
    all_analyzed: list[tuple[str, str]],
    buy_tickers: set[str],
) -> str:
    total = len(all_analyzed)
    lines = [f"📋 <b>분석대상 전체 ({total}종목)</b>  ⭐=상승예측"]

    row: list[str] = []
    for ticker, name in all_analyzed:
        prefix = "⭐" if ticker in buy_tickers else ""
        row.append(f"{prefix}{name}")
        if len(row) == 5:
            lines.append("  ".join(row))
            row = []
    if row:
        lines.append("  ".join(row))

    return "\n".join(lines)


def _market_badge(s: BuySignal) -> str:
    if not s.market:
        return ""
    rank_str = f" {s.mktcap_rank}위" if s.mktcap_rank else ""
    return f"[{s.market}{rank_str}]"


def _loss_pct(s: BuySignal) -> float:
    if s.current_price > 0 and s.stop_loss > 0:
        return (s.current_price - s.stop_loss) / s.current_price
    return 0.03


def _ev_per_day(label: str, prob: float, loss: float) -> float:
    parts = label.split("_")
    d_str, p_str = parts[0], parts[1]
    ev = prob * _GAIN_PCT[p_str] - (1 - prob) * loss
    return ev / _DAYS_MAP[d_str]


def _four_groups(
    signals: list[BuySignal],
) -> tuple[
    list[tuple[BuySignal, float, str]],
    list[tuple[BuySignal, float, str]],
    list[tuple[BuySignal, float, str]],
    list[tuple[BuySignal, float, str]],
]:
    """9라벨 × 전체 종목 케이스에서 그룹별 EV/day 상위 3개 추출.
    종목당 그룹 내 EV 최대 라벨만 유지 (같은 종목 중복 방지)."""
    # buckets: {group_key: {ticker: (signal, ev, label)}}
    buckets: dict[str, dict[str, tuple[BuySignal, float, str]]] = {
        "ls": {}, "lw": {}, "ss": {}, "sw": {},
    }
    for s in signals:
        loss = _loss_pct(s)
        g_prefix = "l" if _is_large_cap(s) else "s"
        for label in _SHORT_LABELS + _SWING_LABELS:
            g_key = g_prefix + ("s" if label in _SHORT_LABELS else "w")
            if s.label_probs:
                prob = s.label_probs.get(label, 0.0)
            elif s.best_label == label and s.xgb_prob is not None:
                prob = s.xgb_prob
            else:
                continue
            if prob <= 0:
                continue
            ev = _ev_per_day(label, prob, loss)
            cur = buckets[g_key].get(s.ticker)
            if cur is None or ev > cur[1]:
                buckets[g_key][s.ticker] = (s, ev, label)

    sort_key = lambda x: (-x[1], -(x[0].xgb_prob or 0))
    return (
        sorted(buckets["ls"].values(), key=sort_key)[:3],
        sorted(buckets["lw"].values(), key=sort_key)[:3],
        sorted(buckets["ss"].values(), key=sort_key)[:3],
        sorted(buckets["sw"].values(), key=sort_key)[:3],
    )


def _is_large_cap(s: BuySignal) -> bool:
    """KOSPI 100위 이내 or KOSDAQ 50위 이내."""
    if not s.market or not s.mktcap_rank:
        return False
    if s.market == "KOSPI":
        return s.mktcap_rank <= 100
    if s.market == "KOSDAQ":
        return s.mktcap_rank <= 50
    return False


def _sort_signals(
    signals: list[BuySignal],
    pr_by_ticker: dict[str, PatternLearningResult],
) -> list[BuySignal]:
    """ML확률 → (days 짧고 % 높은 라벨) → 패턴등급 → 손익비, 상위 10종목 cap."""
    def _key(s: BuySignal):
        ml = -(s.xgb_prob or 0.0)
        lb = _label_tiebreak(s.best_label)
        pr = pr_by_ticker.get(s.ticker)
        pg = _PATTERN_GRADE_ORDER.get(pr.grade, 4) if pr else 4
        rr = -s.risk_reward
        return (ml, lb[0], lb[1], pg, rr)
    return sorted(signals, key=_key)[:10]


def _prediction_summary_section(
    large_short: list[tuple[BuySignal, float, str]],
    large_swing: list[tuple[BuySignal, float, str]],
    small_short: list[tuple[BuySignal, float, str]],
    small_swing: list[tuple[BuySignal, float, str]],
) -> str:
    total = len(large_short) + len(large_swing) + len(small_short) + len(small_swing)
    lines = [f"🎯 <b>S등급 종목 — 기술·거래량 신호 ({total}종목)</b>"]

    def _section(group: list[tuple[BuySignal, float, str]], header: str) -> None:
        if not group:
            return
        lines.append(f"\n{header}")
        for s, _, _ in group:
            badge = _market_badge(s)
            prefix = f"{badge} " if badge else ""
            lines.append(f"{prefix}[{s.grade}급] <b>{s.name}</b> ({s.ticker})")

    _section(large_short, "🏆 <b>대형주 단기상승</b>")
    _section(large_swing,  "🏆 <b>대형주 스윙상승</b>")
    _section(small_short, "📈 <b>중소형주 단기상승</b>")
    _section(small_swing,  "📈 <b>중소형주 스윙상승</b>")
    return "\n".join(lines)


def _stock_entry(
    s: BuySignal,
    pr: Optional[PatternLearningResult],
    group_label: str,
) -> str:
    badge = _market_badge(s)
    badge_str = f"  {badge}" if badge else ""
    pattern_str = f" | 패턴: {s.pattern}" if s.pattern else ""
    pscore_str = f" | 패턴보너스: +{s.pattern_score}" if s.pattern_score > 0 else ""
    star = "⭐ " if s.grade == "S" else ""
    target_line = (
        f"  참고 손절: {s.stop_loss:,.0f}원 | 참고 목표: {s.target_price:,.0f}원\n"
        if s.target_is_resistance
        else f"  참고 손절: {s.stop_loss:,.0f}원\n"
    )
    prob = s.label_probs.get(group_label) if s.label_probs else s.xgb_prob
    ml_line = ""
    if prob is not None:
        label_name = _LABEL_DISPLAY.get(group_label, group_label)
        ev_pct = _ev_per_day(group_label, prob, _loss_pct(s)) * 100
        ml_line = f"  [{label_name}] EV: {ev_pct:.1f}%/일, ML: {prob:.0%}\n"
    entry = (
        f"\n<b>{star}[{s.grade}급] {s.name} ({s.ticker})</b>{badge_str}\n"
        + ml_line +
        f"  추세: {s.trend_score}점 | 거래량: {s.volume_score}점{pattern_str}{pscore_str}\n"
        f"  현재가: {s.current_price:,.0f}원\n"
        + target_line +
        f"  손익비: 약 1:{s.risk_reward}"
    )
    if pr and pr.grade != "INSUFFICIENT":
        pr_em = _PATTERN_GRADE_EMOJI.get(pr.grade, "⚪")
        ret_str = f"+{pr.avg_return_5d:.1f}%" if pr.avg_return_5d >= 0 else f"{pr.avg_return_5d:.1f}%"
        entry += (
            f"\n  {pr_em} 패턴분석: {pr.grade} | "
            f"성공률 {pr.pattern_confidence * 100:.0f}% | "
            f"유사패턴 {pr.similar_count}개 | 평균수익률 {ret_str}"
        )
    return entry


def _buy_detail_section(
    large_short: list[tuple[BuySignal, float, str]],
    large_swing: list[tuple[BuySignal, float, str]],
    small_short: list[tuple[BuySignal, float, str]],
    small_swing: list[tuple[BuySignal, float, str]],
    pr_by_ticker: dict[str, PatternLearningResult],
) -> str:
    lines = ["📊 <b>상승예측 종목 상세</b>"]

    def _section(group: list[tuple[BuySignal, float, str]], header: str) -> None:
        if not group:
            return
        lines.append(f"\n{header}")
        for s, _, lbl in group:
            lines.append(_stock_entry(s, pr_by_ticker.get(s.ticker), lbl))

    _section(large_short, "🏆 <b>대형주 단기상승</b>")
    _section(large_swing,  "🏆 <b>대형주 스윙상승</b>")
    _section(small_short, "📈 <b>중소형주 단기상승</b>")
    _section(small_swing,  "📈 <b>중소형주 스윙상승</b>")
    return "\n".join(lines)


def _build_collect_message(
    total: int,
    ohlcv_ok: int,
    ohlcv_fail: int,
    hourly_ok: int,
    hourly_fail: int,
    index_ok: bool,
    elapsed_sec: int,
) -> str:
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    m, s = divmod(elapsed_sec, 60)
    elapsed_str = f"{m}분 {s}초" if m else f"{s}초"

    def _status(ok, fail):
        if fail == 0:
            return "✅"
        return "⚠️" if ok > 0 else "❌"

    lines = [
        f"📦 <b>데이터 수집 완료 — {today}</b>  (16:00 KST)",
        f"{_status(ohlcv_ok, ohlcv_fail)} 일봉:   {ohlcv_ok}/{total} 종목" + (f"  (실패 {ohlcv_fail})" if ohlcv_fail else ""),
        f"{_status(hourly_ok, hourly_fail)} 60분봉: {hourly_ok}/{total} 종목" + (f"  (실패 {hourly_fail})" if hourly_fail else ""),
        f"{'✅' if index_ok else '❌'} 지수:   {'업데이트 완료' if index_ok else '실패'}",
        f"⏱ 소요시간: {elapsed_str}",
    ]
    return "\n".join(lines)


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks

import json
import logging
from pathlib import Path
from typing import Optional

import requests

import config
from data.db import get_conn
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
    "3d_3pct_clean": "3일+3%", "3d_5pct_clean": "3일+5%", "3d_10pct_clean": "3일+10%",
    "5d_3pct_clean": "5일+3%", "5d_5pct_clean": "5일+5%", "5d_10pct_clean": "5일+10%",
    "10d_3pct_clean": "10일+3%", "10d_5pct_clean": "10일+5%", "10d_10pct_clean": "10일+10%",
    "first_3d_3pct": "3일+3%(첫도달)", "first_3d_5pct": "3일+5%(첫도달)", "first_3d_10pct": "3일+10%(첫도달)",
    "first_5d_3pct": "5일+3%(첫도달)", "first_5d_5pct": "5일+5%(첫도달)", "first_5d_10pct": "5일+10%(첫도달)",
    "first_10d_3pct": "10일+3%(첫도달)", "first_10d_5pct": "10일+5%(첫도달)", "first_10d_10pct": "10일+10%(첫도달)",
}
# 라벨별 XGB+LGBM 평균 AUC — model_registry에서 로드, 없으면 학습 시점 측정값으로 폴백
_LABEL_AUC_FALLBACK = {
    "3d_3pct_clean": 0.553, "3d_5pct_clean": 0.579, "3d_10pct_clean": 0.640,
    "5d_3pct_clean": 0.550, "5d_5pct_clean": 0.559, "5d_10pct_clean": 0.619,
    "10d_3pct_clean": 0.545, "10d_5pct_clean": 0.554, "10d_10pct_clean": 0.574,
    "first_3d_3pct": 0.558, "first_3d_5pct": 0.594, "first_3d_10pct": 0.656,
    "first_5d_3pct": 0.548, "first_5d_5pct": 0.565, "first_5d_10pct": 0.624,
    "first_10d_3pct": 0.543, "first_10d_5pct": 0.552, "first_10d_10pct": 0.575,
}


def _load_label_auc() -> dict[str, float]:
    try:
        conn = get_conn(read_only=True)
        rows = conn.execute(
            "SELECT label_key, AVG(oof_auc) FROM model_registry"
            " WHERE status='production' AND model_type IN ('xgb','lgbm') AND oof_auc IS NOT NULL"
            " GROUP BY label_key"
        ).fetchall()
        conn.close()
        if rows:
            return {k: v for k, v in rows}
    except Exception:
        pass
    return _LABEL_AUC_FALLBACK.copy()


_LABEL_AUC: dict[str, float] = _load_label_auc()
_PATTERN_GRADE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INSUFFICIENT": 3}
_GAIN_PCT     = {"3pct": 0.03, "5pct": 0.05, "10pct": 0.10}
_DAYS_MAP     = {"3d": 3, "5d": 5, "10d": 10}
_SHORT_LABELS = [
    "3d_3pct_clean", "3d_5pct_clean", "3d_10pct_clean",
    "5d_3pct_clean", "5d_5pct_clean", "5d_10pct_clean",
    "first_3d_3pct", "first_3d_5pct", "first_3d_10pct",
    "first_5d_3pct", "first_5d_5pct", "first_5d_10pct",
]
_SWING_LABELS = [
    "10d_3pct_clean", "10d_5pct_clean", "10d_10pct_clean",
    "first_10d_3pct", "first_10d_5pct", "first_10d_10pct",
]

_GATE_VOLUME_MIN = 7
_GATE_TREND_MIN = 6


def _passes_gate(s: BuySignal) -> bool:
    """B등급 최소 기준(bull 기준)과 동일. ML-only(score=0)는 항상 미달."""
    return s.volume_score >= _GATE_VOLUME_MIN and s.trend_score >= _GATE_TREND_MIN


def _group_sort_key(item: tuple) -> tuple:
    s, prob, label = item
    auc = _LABEL_AUC.get(label, 0.5)
    return (-prob * auc, -s.volume_score, -s.trend_score)


def _pick_group(
    bucket: dict[str, tuple],
    used: set[str],
    n: int = 3,
) -> list[tuple]:
    """버킷에서 n개 선택. 게이트 통과 우선, 미달 시 fallback으로 보충."""
    available = [v for v in bucket.values() if v[0].ticker not in used]
    gated = sorted([v for v in available if _passes_gate(v[0])], key=_group_sort_key)
    top = gated[:n]
    if len(top) < n:
        picked = {s.ticker for s, _, _ in top}
        ungated = [v for v in available if not _passes_gate(v[0]) and v[0].ticker not in picked]
        top += sorted(ungated, key=_group_sort_key)[:n - len(top)]
    return top


class ReportAgent:
    """텔레그램 알림 발송 에이전트."""

    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID

    async def send_track_c_report(self, date_str: str) -> bool:
        section = build_track_c_section(date_str)
        if not section:
            return True
        logger.info("ReportAgent: Track C 신호 발송")
        return self._send_chunk(section)

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

    async def send_verify_report(self, ok: int, warn: int, fail: int, lines: list[str]) -> bool:
        message = _build_verify_message(ok, warn, fail, lines)
        logger.info("ReportAgent: 데이터 품질 검증 알림 전송 (OK=%d WARN=%d FAIL=%d)", ok, warn, fail)
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
        groups = _four_groups(buy_signals, pr_by_ticker)
        parts.append(_prediction_summary_section(*groups))
        parts.append(_buy_detail_section(*groups, pr_by_ticker))
    else:
        parts.append("📭 <b>오늘 상승 예측 종목 없음</b>")

    return "\n\n".join(parts)


def _get_data_date() -> str:
    try:
        conn = get_conn(read_only=True)
        row = conn.execute("SELECT MAX(date) FROM universe_features_daily").fetchone()
        conn.close()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return ""


def _header() -> str:
    from datetime import date
    today = date.today()
    weekday = ["월", "화", "수", "목", "금", "토", "일"][today.weekday()]
    today_str = f"{today.strftime('%Y-%m-%d')}({weekday})"

    data_date = _get_data_date()
    data_str = f"{data_date} 종가 기준" if data_date else "전일 종가 기준"

    kst_hour = (config.SCHEDULE_HOUR_UTC + 9) % 24
    time_str = f"KST {kst_hour:02d}:{config.SCHEDULE_MINUTE_UTC:02d}"

    return f"📈 <b>주식 신호 알림 — {today_str}</b>\n  {data_str} | {time_str}"


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


def _parse_label(label: str) -> tuple[str, str]:
    """라벨에서 (days_key, gain_key) 추출. first_ 접두사, _clean 접미사 무시."""
    parts = [p for p in label.split("_") if p not in ("first", "clean")]
    return parts[0], parts[1]


def _ev_per_day(label: str, prob: float, loss: float) -> float:
    d_str, p_str = _parse_label(label)
    ev = prob * _GAIN_PCT[p_str] - (1 - prob) * loss
    return ev / _DAYS_MAP[d_str]


def _label_tiebreak(label: Optional[str]) -> tuple:
    if not label:
        return (999, 0.0)
    try:
        d_str, g_str = _parse_label(label)
    except (IndexError, KeyError):
        return (999, 0.0)
    return (_DAYS_MAP.get(d_str, 999), -_GAIN_PCT.get(g_str, 0.0))


def _four_groups(
    signals: list[BuySignal],
    pr_by_ticker: Optional[dict[str, PatternLearningResult]] = None,
) -> tuple[
    list[tuple[BuySignal, float, str]],
    list[tuple[BuySignal, float, str]],
    list[tuple[BuySignal, float, str]],
    list[tuple[BuySignal, float, str]],
]:
    """ML확률 기준으로 그룹별 상위 3종목 추출.
    단기 먼저 채우고, 단기 선택 ticker는 스윙에서 제외."""
    pr_by_ticker = pr_by_ticker or {}
    # buckets: {group_key: {ticker: (signal, prob, label)}}
    buckets: dict[str, dict[str, tuple[BuySignal, float, str]]] = {
        "ls": {}, "lw": {}, "ss": {}, "sw": {},
    }
    for s in signals:
        g_prefix = "l" if _is_large_cap(s) else "s"
        for label in _SHORT_LABELS + _SWING_LABELS:
            g_key = g_prefix + ("s" if label in _SHORT_LABELS else "w")
            if s.label_probs:
                prob = s.label_probs.get(label, 0.0)
            elif s.best_label == label and s.ensemble_prob is not None:
                prob = s.ensemble_prob
            else:
                continue
            if prob <= 0:
                continue
            cur = buckets[g_key].get(s.ticker)
            if cur is None or prob > cur[1]:
                buckets[g_key][s.ticker] = (s, prob, label)

    # 그룹 선점 순서: 단기/대형 → 단기/중소형 → 스윙/대형 → 스윙/중소형
    used: set[str] = set()
    large_short = _pick_group(buckets["ls"], used)
    used |= {s.ticker for s, _, _ in large_short}

    small_short = _pick_group(buckets["ss"], used)
    used |= {s.ticker for s, _, _ in small_short}

    large_swing = _pick_group(buckets["lw"], used)
    used |= {s.ticker for s, _, _ in large_swing}

    small_swing = _pick_group(buckets["sw"], used)

    return large_short, large_swing, small_short, small_swing


def _is_large_cap(s: BuySignal) -> bool:
    """시총 5조 이상 = 대형주 (KOSPI/KOSDAQ 무관 일관 기준)."""
    return (s.market_cap or 0) >= 5e12


def _sort_signals(
    signals: list[BuySignal],
    pr_by_ticker: dict[str, PatternLearningResult],
) -> list[BuySignal]:
    """AUC가중 ML score → (days 짧고 % 높은 라벨) → 패턴등급 → 손익비, 상위 10종목 cap."""
    def _key(s: BuySignal):
        auc = _LABEL_AUC.get(s.best_label or "", 0.5)
        score = -((s.ensemble_prob or 0.0) * auc)
        lb = _label_tiebreak(s.best_label)
        pr = pr_by_ticker.get(s.ticker)
        pg = _PATTERN_GRADE_ORDER.get(pr.grade, 4) if pr else 4
        rr = -s.risk_reward
        return (score, lb[0], lb[1], pg, rr)
    return sorted(signals, key=_key)[:10]


def _prediction_summary_section(
    large_short: list[tuple[BuySignal, float, str]],
    large_swing: list[tuple[BuySignal, float, str]],
    small_short: list[tuple[BuySignal, float, str]],
    small_swing: list[tuple[BuySignal, float, str]],
) -> str:
    total = len(large_short) + len(large_swing) + len(small_short) + len(small_swing)
    lines = [f"🎯 <b>매수 추천 — 규칙+ML 신호 ({total}종목)</b>"]

    def _section(group: list[tuple[BuySignal, float, str]], header: str) -> None:
        if not group:
            return
        lines.append(f"\n{header}")
        for s, _, _ in group:
            badge = _market_badge(s)
            prefix = f"{badge} " if badge else ""
            grade_str = s.grade if s.grade == "ML" else f"{s.grade}급"
            lines.append(f"{prefix}[{grade_str}] <b>{s.name}</b> ({s.ticker})")

    _section(large_short, "🏆 <b>대형주 단기상승</b>")
    _section(small_short, "📈 <b>중소형주 단기상승</b>")
    _section(large_swing, "🏆 <b>대형주 스윙상승</b>")
    _section(small_swing, "📈 <b>중소형주 스윙상승</b>")
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
    grade_str = s.grade if s.grade == "ML" else f"{s.grade}급"
    target_line = (
        f"  참고 손절: {s.stop_loss:,.0f}원 | 참고 목표: {s.target_price:,.0f}원\n"
        if s.target_is_resistance
        else f"  참고 손절: {s.stop_loss:,.0f}원\n"
    )
    prob = s.label_probs.get(group_label) if s.label_probs else s.ensemble_prob
    ml_line = ""
    if prob is not None:
        label_name = _LABEL_DISPLAY.get(group_label, group_label)
        ev_pct = _ev_per_day(group_label, prob, _loss_pct(s)) * 100
        auc = _LABEL_AUC.get(group_label, 0.5)
        ml_line = f"  [{label_name}] ML: {prob:.0%} (AUC {auc:.2f}) | EV: {ev_pct:.1f}%/일\n"
    entry = (
        f"\n<b>{star}[{grade_str}] {s.name} ({s.ticker})</b>{badge_str}\n"
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
    _section(small_short, "📈 <b>중소형주 단기상승</b>")
    _section(large_swing, "🏆 <b>대형주 스윙상승</b>")
    _section(small_swing, "📈 <b>중소형주 스윙상승</b>")
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


def _build_verify_message(ok: int, warn: int, fail: int, lines: list[str]) -> str:
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    icon = "❌" if fail > 0 else ("⚠️" if warn > 0 else "✅")
    parts = [
        f"🔍 <b>데이터 품질 검증 — {today}</b>",
        f"{icon} OK {ok} / WARN {warn} / FAIL {fail}",
    ]
    problems = [l for l in lines if l.startswith("[WARN]") or l.startswith("[FAIL]")]
    if problems:
        parts.append("\n".join(f"  {l}" for l in problems))
    return "\n".join(parts)


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


# ── Track C ───────────────────────────────────────────────────────────────────

_C_LABEL_DISPLAY = {
    "label_3d_5pct_first": "3일+5%",
    "label_3d_10pct_first_c": "3일+10%",
    "label_3d_trend_start_atr": "3일추세시작",
    "label_5d_7pct_first": "5일+7%",
    "label_5d_10pct_first_c": "5일+10%",
    "label_2d_5pct_first": "2일+5%",
    "label_1d_5pct_first": "1일+5%",
    "label_3d_sector_excess_top30pct": "3일섹터상위30%",
    "label_5d_sector_excess_top20pct": "5일섹터상위20%",
    "label_3d_bb_upper_break": "BB상단돌파",
    "label_3d_range_breakout_20d": "20일박스3d",
    "label_3d_bb_squeeze_breakout": "BB수렴돌파3d",
    "label_5d_bb_squeeze_breakout": "BB수렴돌파5d",
    "label_5d_range_breakout_20d": "20일박스5d",
    "label_3d_recover_pullback": "눌림목회복",
    "label_2d_volume_surge_5pct": "거래량급증+5%",
}

_C_MODEL_RESULTS_PATH = Path("data/model_results_c.json")
_C_TOP_N = 10


def _load_c_auc_weights() -> dict[str, float]:
    """model_results_c.json에서 label별 AUC 가중치 로드. soft_auc 우선, 없으면 xgb/lgbm/et 평균."""
    try:
        with open(_C_MODEL_RESULTS_PATH, encoding="utf-8") as f:
            results = json.load(f)
        weights = {}
        for row in results:
            label = row.get("target", "")
            if not label:
                continue
            auc = row.get("soft_auc")
            if auc is None:
                vals = [row[k] for k in ("xgb_auc", "lgbm_auc", "et_auc") if row.get(k)]
                auc = sum(vals) / len(vals) if vals else None
            if auc:
                weights[label] = float(auc)
        return weights
    except Exception:
        return {}


def _c_composite_score(label_probs: dict[str, float], auc_weights: dict[str, float]) -> float:
    """AUC-가중 확률 합산. 각 라벨의 prob × AUC 합계."""
    return sum(prob * auc_weights.get(label, 0.5) for label, prob in label_probs.items())


def _c_ticker_name(ticker: str) -> str:
    try:
        from pykrx import stock
        return stock.get_market_ticker_name(ticker) or ticker
    except Exception:
        return ticker


def build_track_c_section(date_str: str, top_n: int = _C_TOP_N) -> str:
    """signal_history_c → 종합점수 Top-N 텔레그램 섹션 생성."""
    conn = get_conn(read_only=True)
    try:
        rows = conn.execute(
            "SELECT ticker, label_probs FROM signal_history_c WHERE signal_date = CAST(? AS DATE)",
            [date_str],
        ).fetchall()
    except Exception as e:
        logger.warning("signal_history_c 조회 실패: %s", e)
        return ""
    finally:
        conn.close()

    if not rows:
        return f"🤖 <b>Track C — {date_str} 신호 없음</b>"

    auc_weights = _load_c_auc_weights()

    scored: list[tuple[str, float, dict]] = []
    for ticker, label_probs_raw in rows:
        try:
            lp = json.loads(label_probs_raw) if isinstance(label_probs_raw, str) else label_probs_raw
        except Exception:
            continue
        scored.append((ticker, _c_composite_score(lp, auc_weights), lp))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:top_n]

    lines = [
        f"🤖 <b>Track C 신호 — {date_str}</b>  ({len(scored)}종목 중 상위 {len(top)}개)"
    ]
    for rank, (ticker, score, lp) in enumerate(top, 1):
        name = _c_ticker_name(ticker)
        top3 = sorted(lp.items(), key=lambda x: x[1], reverse=True)[:3]
        label_str = "  ".join(
            f"{_C_LABEL_DISPLAY.get(lbl, lbl)} {p:.0%}" for lbl, p in top3
        )
        lines.append(f"\n{rank}. <b>{name} ({ticker})</b>  점수 {score:.3f}\n   {label_str}")

    return "\n".join(lines)

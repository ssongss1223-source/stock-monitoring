import logging
from typing import Optional

import requests

import config
from models.signals import BuySignal, MarketContext, PatternLearningResult, SellSignal

logger = logging.getLogger(__name__)

_MARKET_EMOJI = {"bull": "✅", "sideways": "⚠️", "bear": "❌"}
_GRADE_EMOJI = {"S": "⭐⭐⭐", "A": "⭐⭐", "B": "⭐"}
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
    parts = [_header(), _market_section(markets)]

    if sell_signals:
        parts.append(_sell_section(sell_signals))

    # ① 3일내 +3% 이상 상승 예측 종목 요약
    if buy_signals:
        parts.append(_prediction_summary_section(buy_signals))
    else:
        parts.append("📭 <b>오늘 상승 예측 종목 없음</b>")

    # ③ 상승 예측 종목 상세 정보 (패턴분석 포함)
    if buy_signals:
        pr_by_ticker = {pr.ticker: pr for pr in (pattern_results or [])}
        parts.append(_buy_detail_section(buy_signals, pr_by_ticker))

    return "\n\n".join(parts)


def _header() -> str:
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    return f"📈 <b>주식 신호 알림 — {today}</b>\n  전일 종가 기준 | KST 08:00"


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


def _prediction_summary_section(signals: list[BuySignal]) -> str:
    grade_order = {"S": 0, "A": 1, "B": 2}
    sorted_signals = sorted(signals, key=lambda s: (grade_order.get(s.grade, 9), -s.total_score))

    count = len(sorted_signals)
    lines = [f"🎯 <b>3일내 +3% 이상 상승 예측 종목 ({count}종목)</b>"]
    for s in sorted_signals:
        em = _GRADE_EMOJI.get(s.grade, "")
        upside = round((s.target_price / s.current_price - 1) * 100, 1) if s.current_price > 0 else 0
        lines.append(f"{em} [{s.grade}급] <b>{s.name}</b> ({s.ticker}) — 목표 +{upside}%")
    return "\n".join(lines)


def _buy_detail_section(
    signals: list[BuySignal],
    pr_by_ticker: dict[str, PatternLearningResult],
) -> str:
    grade_order = {"S": 0, "A": 1, "B": 2}
    sorted_signals = sorted(signals, key=lambda s: (grade_order.get(s.grade, 9), -s.total_score))

    lines = ["📊 <b>상승예측 종목 상세</b>"]
    for s in sorted_signals:
        em = _GRADE_EMOJI.get(s.grade, "")
        pattern_str = f" | 패턴: {s.pattern}" if s.pattern else ""
        pscore_str = f" | 패턴보너스: +{s.pattern_score}" if s.pattern_score > 0 else ""
        entry = (
            f"\n{em} <b>[{s.grade}급] {s.name} ({s.ticker})</b>\n"
            f"  추세: {s.trend_score}점 | 거래량: {s.volume_score}점{pattern_str}{pscore_str}\n"
            f"  현재가: {s.current_price:,.0f}원\n"
            f"  참고 손절: {s.stop_loss:,.0f}원 | 참고 목표: {s.target_price:,.0f}원\n"
            f"  손익비: 약 1:{s.risk_reward}"
        )
        if s.xgb_prob is not None:
            entry += f"\n  ML(3일+5%): {s.xgb_prob:.0%}"
        pr = pr_by_ticker.get(s.ticker)
        if pr and pr.grade != "INSUFFICIENT":
            pr_em = _PATTERN_GRADE_EMOJI.get(pr.grade, "⚪")
            ret_str = f"+{pr.avg_return_5d:.1f}%" if pr.avg_return_5d >= 0 else f"{pr.avg_return_5d:.1f}%"
            entry += (
                f"\n  {pr_em} 패턴분석: {pr.grade} | "
                f"성공률 {pr.pattern_confidence * 100:.0f}% | "
                f"유사패턴 {pr.similar_count}개 | 평균수익률 {ret_str}"
            )
        lines.append(entry)

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

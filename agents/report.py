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


class ReportAgent:
    """전략서 12장 포맷으로 텔레그램 알림을 발송한다."""

    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID

    async def send(
        self,
        markets: dict[str, MarketContext],
        buy_signals: list[BuySignal],
        sell_signals: list[SellSignal],
        pattern_results: list[PatternLearningResult] | None = None,
    ) -> bool:
        message = _build_message(markets, buy_signals, sell_signals, pattern_results)
        logger.info("ReportAgent: 메시지 %d자 전송 시도", len(message))

        # Telegram 메시지 4096자 제한 → 필요 시 분할
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
) -> str:
    parts = [_header(), _market_section(markets)]

    if sell_signals:
        parts.append(_sell_section(sell_signals))

    if buy_signals:
        parts.append(_buy_section(buy_signals))
    else:
        parts.append("📭 <b>오늘 매수 추천 없음</b>")

    if pattern_results:
        section = _pattern_section(pattern_results)
        if section:
            parts.append(section)

    return "\n\n".join(parts)


def _header() -> str:
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    return f"📈 <b>주식 신호 알림 — {today}</b>"


def _market_section(markets: dict[str, MarketContext]) -> str:
    lines = ["🌍 <b>장세 판단</b>"]
    for name, ctx in markets.items():
        em = _MARKET_EMOJI.get(ctx.market_status, "❓")
        lines.append(
            f"  {name}: {em} {ctx.market_status.upper()} "
            f"(점수 {ctx.score}점, 페널티 +{ctx.market_bias})"
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


def _buy_section(signals: list[BuySignal]) -> str:
    # S → A → B 순 정렬
    grade_order = {"S": 0, "A": 1, "B": 2}
    sorted_signals = sorted(signals, key=lambda s: (grade_order.get(s.grade, 9), -s.total_score))

    lines = ["📌 <b>매수 추천</b>"]
    for s in sorted_signals:
        em = _GRADE_EMOJI.get(s.grade, "")
        pattern_str = f" | 패턴: {s.pattern}" if s.pattern else ""
        lines.append(
            f"\n{em} <b>[{s.grade}급] {s.name} ({s.ticker})</b>\n"
            f"  추세: {s.trend_score}점 | 거래량: {s.volume_score}점{pattern_str}\n"
            f"  현재가: {s.current_price:,.0f}원\n"
            f"  참고 손절: {s.stop_loss:,.0f}원 | 참고 목표: {s.target_price:,.0f}원\n"
            f"  손익비: 약 1:{s.risk_reward}"
        )
    return "\n".join(lines)


_PATTERN_GRADE_EMOJI = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}
_PATTERN_GRADE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INSUFFICIENT": 3}


def _pattern_section(results: list[PatternLearningResult]) -> str:
    visible = [r for r in results if r.grade != "INSUFFICIENT"]
    if not visible:
        return ""
    visible.sort(key=lambda r: (_PATTERN_GRADE_ORDER.get(r.grade, 9), -r.pattern_confidence))
    lines = ["📊 <b>종목별 패턴 분석 (5일 +5% 기준)</b>"]
    for r in visible:
        em = _PATTERN_GRADE_EMOJI.get(r.grade, "⚪")
        ret_str = f"+{r.avg_return_5d:.1f}%" if r.avg_return_5d >= 0 else f"{r.avg_return_5d:.1f}%"
        lines.append(
            f"{em} <b>{r.ticker}</b>: {r.grade} | "
            f"성공률 {r.pattern_confidence * 100:.0f}% | "
            f"유사패턴 {r.similar_count}개 | "
            f"평균수익률 {ret_str} | "
            f"W={r.optimal_window} | {r.pattern_dim}"
        )
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

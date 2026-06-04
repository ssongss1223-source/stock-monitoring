# backtest/sma_reporter_v2.py
"""SMA v2 백테스트 결과 텔레그램 리포트 — 3단 표 (SMA단독/눌림목/종합)."""
from __future__ import annotations


def format_report(
    ticker: str,
    name: str,
    agg: dict[str, dict],  # aggregate_wf_results() 반환값
    best_bo_params: dict | None,
    best_pb_params: dict | None,
) -> str:
    """3단 표 텔레그램 메시지 포맷.

    Args:
        ticker: 종목코드
        name: 종목명
        agg: {"sma_breakout": metrics, "pullback": metrics, "combined": metrics}
        best_bo_params: SMA breakout 최빈 파라미터 (sma_period)
        best_pb_params: pullback 최빈 파라미터 (sma_period, delta, stop)
    """
    bo = agg.get("sma_breakout", {})
    pb = agg.get("pullback", {})
    cb = agg.get("combined", {})

    wf_label = "WF" if bo.get("is_walkforward") else "인샘플"

    bo_param_str = f"SMA{best_bo_params['sma_period']}" if best_bo_params else "-"
    pb_param_str = (
        f"SMA{best_pb_params['sma_period']} Δ{best_pb_params['pullback_sma_delta']} "
        f"stop{best_pb_params['stop_loss_pct']:.0f}%"
        if best_pb_params else "-"
    )

    def row(label: str, m: dict) -> str:
        calmar = m.get("calmar", 0)
        cagr   = m.get("cagr", 0)
        mdd    = m.get("mdd", 0)
        vs_bh  = m.get("vs_buyhold", 0)
        trades = m.get("total_trades", 0)
        sign   = "+" if vs_bh >= 0 else ""
        flag   = "[O]" if calmar > 0.5 else ("[!]" if calmar > 0.2 else "[X]")
        return (
            f"{flag} {label:<6} | Calmar {calmar:5.2f} | "
            f"CAGR {cagr:+.1f}% | MDD {mdd:.1f}% | "
            f"vsBH {sign}{vs_bh:.1f}% | {trades}건"
        )

    lines = [
        f"[SMA v2] [{ticker}] {name}  ({wf_label})",
        f"  SMA breakout: {bo_param_str}",
        f"  Pullback: {pb_param_str}",
        "─" * 52,
        row("SMA", bo),
        row("눌림목", pb),
        row("종합", cb),
    ]
    return "\n".join(lines)


def format_summary(results: list[dict]) -> str:
    """전체 종목 요약 메시지."""
    total = len(results)
    beat_bh = sum(1 for r in results if r.get("combined_vs_bh", 0) > 0)
    avg_calmar = (
        sum(r.get("combined_calmar", 0) for r in results) / total if total else 0
    )
    lines = [
        f"[SMA v2] 백테스트 완료 -- {total}종목",
        f"  buy&hold 초과: {beat_bh}/{total}종목",
        f"  종합 평균 Calmar: {avg_calmar:.3f}",
    ]
    return "\n".join(lines)

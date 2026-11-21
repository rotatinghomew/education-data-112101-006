"""变化解释：比较两个口径日期之间可见交易状态的差异。

结算回补或退款状态变化后，用本模块说明受影响的销售记录与资金期间，
以及每项变化来自哪笔交易。
"""
from __future__ import annotations

from datetime import date

from .loader import Dataset
from .views import (
    CLASS_LABELS,
    classify_order,
    month_key,
    signing_contribution,
)


def explain(ds: Dataset, d_from: date, d_to: date) -> dict:
    """列出 (d_from, d_to] 窗口内改变核算结果的交易。"""
    changes: list[dict] = []

    for order in ds.orders:
        if order.ordered_at and d_from < order.ordered_at <= d_to:
            cls = classify_order(order, ds.orders_by_ref)
            contribution = signing_contribution(order, ds.orders_by_ref)
            period = month_key(order.ordered_at)
            changes.append(
                {
                    "date": order.ordered_at.isoformat(),
                    "kind": "order_created",
                    "ref": order.order_ref,
                    "order_ref": order.order_ref,
                    "amount": order.gross_amount or 0,
                    "period": period,
                    "cash_impact": 0,
                    "sales_impact": contribution,
                    "description": (
                        f"订单 {order.order_ref} 创建（{CLASS_LABELS[cls]}，"
                        f"计入 {period} 销售期，新签贡献 {contribution:,}）"
                    ),
                }
            )

    for payment in ds.payments:
        if not payment.settled_at or not (d_from < payment.settled_at <= d_to):
            continue
        period = month_key(payment.settled_at)
        if payment.is_reversal:
            changes.append(
                {
                    "date": payment.settled_at.isoformat(),
                    "kind": "reversal_executed",
                    "ref": payment.payment_ref,
                    "order_ref": payment.order_ref,
                    "amount": payment.amount or 0,
                    "period": period,
                    "cash_impact": -(payment.amount or 0),
                    "sales_impact": 0,
                    "description": (
                        f"付款冲正 {payment.payment_ref} 生效（冲正 "
                        f"{payment.reversal_of}，订单 {payment.order_ref}，"
                        f"{period} 资金 -{payment.amount or 0:,}）"
                    ),
                }
            )
        else:
            changes.append(
                {
                    "date": payment.settled_at.isoformat(),
                    "kind": "payment_settled",
                    "ref": payment.payment_ref,
                    "order_ref": payment.order_ref,
                    "amount": payment.amount or 0,
                    "period": period,
                    "cash_impact": payment.amount or 0,
                    "sales_impact": 0,
                    "description": (
                        f"付款 {payment.payment_ref} 结算（订单 "
                        f"{payment.order_ref}，{period} 资金 "
                        f"+{payment.amount or 0:,}）"
                    ),
                }
            )

    for refund in ds.refunds:
        settled_in_window = refund.settled_at and d_from < refund.settled_at <= d_to
        requested_in_window = (
            refund.requested_at and d_from < refund.requested_at <= d_to
        )
        if settled_in_window:
            period = month_key(refund.settled_at)
            changes.append(
                {
                    "date": refund.settled_at.isoformat(),
                    "kind": "refund_settled",
                    "ref": refund.refund_ref,
                    "order_ref": refund.order_ref,
                    "amount": refund.amount or 0,
                    "period": period,
                    "cash_impact": -(refund.amount or 0),
                    "sales_impact": 0,
                    "description": (
                        f"退款 {refund.refund_ref} 结算（订单 {refund.order_ref}，"
                        f"{period} 资金 -{refund.amount or 0:,}，"
                        f"由未结算转为已结算）"
                    ),
                }
            )
        elif requested_in_window:
            changes.append(
                {
                    "date": refund.requested_at.isoformat(),
                    "kind": "refund_requested",
                    "ref": refund.refund_ref,
                    "order_ref": refund.order_ref,
                    "amount": refund.amount or 0,
                    "period": None,
                    "cash_impact": 0,
                    "sales_impact": 0,
                    "description": (
                        f"退款 {refund.refund_ref} 申请（订单 {refund.order_ref}，"
                        f"金额 {refund.amount or 0:,}，列为未结算，不计现金流）"
                    ),
                }
            )

    changes.sort(key=lambda c: (c["date"], c["ref"]))

    cash_delta: dict[str, int] = {}
    sales_delta: dict[str, int] = {}
    for change in changes:
        if change["cash_impact"] and change["period"]:
            cash_delta[change["period"]] = (
                cash_delta.get(change["period"], 0) + change["cash_impact"]
            )
        if change["sales_impact"] and change["period"]:
            sales_delta[change["period"]] = (
                sales_delta.get(change["period"], 0) + change["sales_impact"]
            )

    return {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "changes": changes,
        "cash_delta_by_period": dict(sorted(cash_delta.items())),
        "sales_delta_by_period": dict(sorted(sales_delta.items())),
    }


def render_explain_text(result: dict) -> str:
    lines = [f"口径变化解释：{result['from']} → {result['to']}", ""]
    if not result["changes"]:
        lines.append("  窗口内没有影响核算结果的交易变化。")
    for change in result["changes"]:
        lines.append(f"  {change['date']}  {change['description']}")
    if result["cash_delta_by_period"]:
        lines.append("")
        lines.append("资金期间净变化：")
        for period, delta in result["cash_delta_by_period"].items():
            lines.append(f"  {period}：{delta:+,}")
    if result["sales_delta_by_period"]:
        lines.append("")
        lines.append("销售期间新签净变化（去重口径）：")
        for period, delta in result["sales_delta_by_period"].items():
            lines.append(f"  {period}：{delta:+,}")
    return "\n".join(lines)

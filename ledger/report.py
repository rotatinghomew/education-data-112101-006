"""经营复盘报告：把销售、资金、履约三种视角汇总为可核对的结构。

订单额、服务履约与实际现金在报告中分列，不合并为单一“收入”数字。
传入 as_of 口径日期可按当时可见的交易状态还原已发布的复盘结果。
"""
from __future__ import annotations

from datetime import date

from .loader import Dataset
from .views import (
    CLASS_LABELS,
    DISPUTED,
    NEW,
    RENEWAL,
    UPGRADE,
    cash_events,
    classify_order,
    entitlement_lines,
    month_key,
    pending_refunds,
    quarter_of_month,
    signing_contribution,
)

_CLASSES = (NEW, UPGRADE, RENEWAL)


def _money(amount: int) -> str:
    return f"{amount:,}"


def _empty_sales_bucket() -> dict:
    bucket = {cls: {"count": 0, "amount": 0, "gross": 0} for cls in _CLASSES}
    bucket.update({"signed_total": 0, "disputed_count": 0, "disputed_amount": 0})
    return bucket


def _empty_cash_bucket() -> dict:
    return {
        "receipts": 0,
        "reversals": 0,
        "refunds": 0,
        "net": 0,
        "disputed_net": 0,
        "pending_refunds": 0,
        "disputed_gross": 0,
    }


def _merge_sales_bucket(target: dict, source: dict) -> None:
    for cls in _CLASSES:
        target[cls]["count"] += source[cls]["count"]
        target[cls]["amount"] += source[cls]["amount"]
        target[cls]["gross"] += source[cls]["gross"]
    target["signed_total"] += source["signed_total"]
    target["disputed_count"] += source["disputed_count"]
    target["disputed_amount"] += source["disputed_amount"]


def _merge_cash_bucket(target: dict, source: dict) -> None:
    for key in target:
        target[key] += source[key]


def build_report(ds: Dataset, as_of: date | None = None) -> dict:
    """生成复盘报告结构；as_of 给定后只使用当时可见的交易状态。"""
    orders = [
        o
        for o in ds.orders
        if o.ordered_at is not None and (as_of is None or o.ordered_at <= as_of)
    ]
    events = cash_events(ds, as_of)
    pending = pending_refunds(ds, as_of)
    disputed_refs = {o.order_ref for o in ds.orders if o.state == DISPUTED}

    # ---- 销售表现：按订单创建时间归期
    sales_monthly: dict[str, dict] = {}
    sales_by_method: dict[str, dict] = {}
    for order in orders:
        cls = classify_order(order, ds.orders_by_ref)
        contribution = signing_contribution(order, ds.orders_by_ref)
        gross = order.gross_amount or 0
        period = month_key(order.ordered_at)
        method = ds.sales_method(order.order_ref)
        for bucket in (
            sales_monthly.setdefault(period, _empty_sales_bucket()),
            sales_by_method.setdefault(method, _empty_sales_bucket()),
        ):
            bucket[cls]["count"] += 1
            bucket[cls]["amount"] += contribution
            bucket[cls]["gross"] += gross
            bucket["signed_total"] += contribution
            if order.state == DISPUTED:
                bucket["disputed_count"] += 1
                bucket["disputed_amount"] += gross

    sales_quarterly: dict[str, dict] = {}
    for period, bucket in sales_monthly.items():
        quarter = sales_quarterly.setdefault(
            quarter_of_month(period), _empty_sales_bucket()
        )
        _merge_sales_bucket(quarter, bucket)

    # ---- 资金变化：按实际结算时间归期
    cash_monthly: dict[str, dict] = {}
    cash_by_method: dict[str, dict] = {}
    for event in events:
        period = month_key(event.occurred_at)
        method = ds.sales_method(event.order_ref)
        for bucket in (
            cash_monthly.setdefault(period, _empty_cash_bucket()),
            cash_by_method.setdefault(method, _empty_cash_bucket()),
        ):
            if event.kind == "payment":
                bucket["receipts"] += event.amount
            elif event.kind == "reversal":
                bucket["reversals"] += -event.amount
            elif event.kind == "refund":
                bucket["refunds"] += -event.amount
            bucket["net"] += event.amount
            if event.order_ref in disputed_refs:
                bucket["disputed_net"] += event.amount

    cash_quarterly: dict[str, dict] = {}
    for period, bucket in cash_monthly.items():
        quarter = cash_quarterly.setdefault(
            quarter_of_month(period), _empty_cash_bucket()
        )
        _merge_cash_bucket(quarter, bucket)

    # ---- 尚未结算与争议：单独列示，不计入现金流
    pending_rows = []
    for refund in sorted(pending, key=lambda r: r.refund_ref):
        order = ds.orders_by_ref.get(refund.order_ref)
        method = ds.sales_method(refund.order_ref)
        pending_rows.append(
            {
                "refund_ref": refund.refund_ref,
                "order_ref": refund.order_ref,
                "amount": refund.amount or 0,
                "requested_at": refund.requested_at.isoformat()
                if refund.requested_at
                else None,
                "sales_method": method,
                "sales_period": month_key(order.ordered_at) if order and order.ordered_at else None,
            }
        )
        cash_by_method.setdefault(method, _empty_cash_bucket())[
            "pending_refunds"
        ] += refund.amount or 0

    settled_net_by_order: dict[str, int] = {}
    for event in events:
        settled_net_by_order[event.order_ref] = (
            settled_net_by_order.get(event.order_ref, 0) + event.amount
        )
    pending_by_order: dict[str, int] = {}
    for refund in pending:
        pending_by_order[refund.order_ref] = (
            pending_by_order.get(refund.order_ref, 0) + (refund.amount or 0)
        )
    disputed_rows = []
    for order in orders:
        if order.state != DISPUTED:
            continue
        method = ds.sales_method(order.order_ref)
        cash_by_method.setdefault(method, _empty_cash_bucket())[
            "disputed_gross"
        ] += order.gross_amount or 0
        disputed_rows.append(
            {
                "order_ref": order.order_ref,
                "gross_amount": order.gross_amount or 0,
                "sales_method": method,
                "sales_period": month_key(order.ordered_at),
                "settled_net": settled_net_by_order.get(order.order_ref, 0),
                "pending_refund_amount": pending_by_order.get(order.order_ref, 0),
            }
        )

    # ---- 服务履约：有效服务单位，与金额和现金相互独立
    lines = entitlement_lines(ds, orders)
    fulfillment_by_state: dict[str, dict] = {}
    for line in lines:
        bucket = fulfillment_by_state.setdefault(line.state, {"orders": 0, "units": 0})
        bucket["orders"] += 1
        bucket["units"] += line.units

    return {
        "as_of": as_of.isoformat() if as_of else None,
        "sales": {
            "monthly": [
                {"period": period, **sales_monthly[period]}
                for period in sorted(sales_monthly)
            ],
            "quarterly": [
                {"period": period, **sales_quarterly[period]}
                for period in sorted(sales_quarterly)
            ],
            "by_method": [
                {"method": method, **sales_by_method[method]}
                for method in sorted(sales_by_method)
            ],
        },
        "cash": {
            "monthly": [
                {"period": period, **cash_monthly[period]}
                for period in sorted(cash_monthly)
            ],
            "quarterly": [
                {"period": period, **cash_quarterly[period]}
                for period in sorted(cash_quarterly)
            ],
            "by_method": [
                {"method": method, **cash_by_method[method]}
                for method in sorted(cash_by_method)
            ],
        },
        "fulfillment": {
            "orders_counted": len(lines),
            "total_units": sum(line.units for line in lines),
            "by_state": fulfillment_by_state,
            "lines": [line.__dict__ for line in lines],
        },
        "outstanding": {
            "pending_refunds": pending_rows,
            "pending_refunds_total": sum(row["amount"] for row in pending_rows),
            "disputed_orders": disputed_rows,
            "disputed_gross_total": sum(row["gross_amount"] for row in disputed_rows),
        },
        "warnings": list(ds.warnings),
    }


# ---------------------------------------------------------------- 文本呈现

def _sales_row_text(row: dict) -> str:
    cells = [row["period"]]
    for cls in _CLASSES:
        part = row[cls]
        cells.append(f"{CLASS_LABELS[cls]} {part['count']} 笔 / {_money(part['amount'])}")
    cells.append(f"新签合计 {_money(row['signed_total'])}")
    cells.append(f"其中争议 {_money(row['disputed_amount'])}")
    return "  |  ".join(cells)


def _cash_row_text(row: dict) -> str:
    reversals = f"-{_money(row['reversals'])}" if row["reversals"] else "0"
    refunds = f"-{_money(row['refunds'])}" if row["refunds"] else "0"
    return (
        f"{row['period']}  |  收款 {_money(row['receipts'])}  |  "
        f"冲正 {reversals}  |  退款 {refunds}  |  "
        f"净额 {_money(row['net'])}  |  其中争议相关 {_money(row['disputed_net'])}"
    )


def render_text(report: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * 72)
    as_of = report["as_of"] or "最新资料状态"
    add(f"季度经营复盘报告（口径日期：{as_of}）")
    add("订单额、服务履约与实际现金分列，不合并为单一“收入”数字。")
    add("=" * 72)

    add("")
    add("一、销售表现（按订单创建时间归入销售期；升级单仅计增量，同一原有服务不重复计入）")
    add("")
    add("【月度】期间 | 新购 | 升级 | 续单 | 新签合计(去重) | 其中争议订单额")
    for row in report["sales"]["monthly"]:
        add("  " + _sales_row_text(row))
    add("【季度】")
    for row in report["sales"]["quarterly"]:
        add("  " + _sales_row_text(row))
    add("【按销售方式】方式 | 新购 | 升级 | 续单 | 新签合计(去重) | 其中争议订单额")
    for row in report["sales"]["by_method"]:
        add("  " + _sales_row_text({"period": row["method"], **row}))

    add("")
    add("二、服务履约（有效服务单位；部分退款不抹掉仍然有效的服务）")
    fulfillment = report["fulfillment"]
    add(
        f"  有效订单 {fulfillment['orders_counted']} 笔，"
        f"服务单位合计 {fulfillment['total_units']}"
    )
    for state, bucket in sorted(fulfillment["by_state"].items()):
        add(f"    状态 {state}：{bucket['orders']} 笔 / {bucket['units']} 单位")
    flagged = [line for line in fulfillment["lines"] if line["flag"]]
    for line in flagged:
        add(
            f"    提示：订单 {line['order_ref']}（{line['course_ref']} "
            f"版本 {line['version'] or '未知'}，{line['units']} 单位）{line['flag']}"
        )

    add("")
    add("三、资金变化（按实际结算时间归期；未结算退款与争议款项不计入）")
    add("")
    add("【月度】")
    for row in report["cash"]["monthly"]:
        add("  " + _cash_row_text(row))
    add("【季度】")
    for row in report["cash"]["quarterly"]:
        add("  " + _cash_row_text(row))
    add("【按销售方式】")
    for row in report["cash"]["by_method"]:
        reversals = f"-{_money(row['reversals'])}" if row["reversals"] else "0"
        refunds = f"-{_money(row['refunds'])}" if row["refunds"] else "0"
        add(
            f"  {row['method']}  |  收款 {_money(row['receipts'])}  |  "
            f"冲正 {reversals}  |  退款 {refunds}  |  "
            f"净额 {_money(row['net'])}  |  未结算退款 {_money(row['pending_refunds'])}  |  "
            f"争议订单额 {_money(row['disputed_gross'])}"
        )

    add("")
    add("四、尚未结算与争议（单独列示，不作为已发生的现金流）")
    outstanding = report["outstanding"]
    add(f"  未结算退款合计 {_money(outstanding['pending_refunds_total'])}：")
    for row in outstanding["pending_refunds"]:
        add(
            f"    {row['refund_ref']}（订单 {row['order_ref']}，{_money(row['amount'])}，"
            f"申请于 {row['requested_at'] or '未知'}，销售期 {row['sales_period'] or '未知'}，"
            f"{row['sales_method']}）"
        )
    add(f"  争议订单合计 {_money(outstanding['disputed_gross_total'])}：")
    for row in outstanding["disputed_orders"]:
        add(
            f"    {row['order_ref']}（订单额 {_money(row['gross_amount'])}，"
            f"销售期 {row['sales_period']}，{row['sales_method']}，"
            f"已结算净额 {_money(row['settled_net'])}，"
            f"未结算退款 {_money(row['pending_refund_amount'])}）"
        )

    if report["warnings"]:
        add("")
        add("五、数据质量提示（不阻断核算，供追溯时参考）")
        for warning in report["warnings"]:
            add(f"  - {warning}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 订单追溯

def order_detail(ds: Dataset, order_ref: str) -> dict:
    """单笔订单的追溯明细：父子链、课程版本、付款人、付款与退款。"""
    order = ds.orders_by_ref[order_ref]
    session = ds.sessions_by_ref.get(order.session_ref)
    chain = []
    current = order
    while current.parent_order and current.parent_order in ds.orders_by_ref:
        current = ds.orders_by_ref[current.parent_order]
        chain.append(current.order_ref)
    children = [child.order_ref for child in ds.children_by_parent.get(order_ref, [])]
    from .views import resolve_course_version  # 避免模块级循环导入

    version = resolve_course_version(ds, order.course_ref, order.ordered_at)
    payments = [p for p in ds.payments if p.order_ref == order_ref]
    refunds = [r for r in ds.refunds if r.order_ref == order_ref]
    return {
        "order_ref": order.order_ref,
        "household_ref": order.household_ref,
        "course_ref": order.course_ref,
        "course_version": version.version if version else None,
        "course_units": (version.units or 0) if version else 0,
        "state": order.state,
        "ordered_at": order.ordered_at.isoformat() if order.ordered_at else None,
        "gross_amount": order.gross_amount,
        "class": classify_order(order, ds.orders_by_ref),
        "signing_contribution": signing_contribution(order, ds.orders_by_ref),
        "parents": chain,
        "children": children,
        "session": session.__dict__ if session else None,
        "payments": [
            {
                "payment_ref": p.payment_ref,
                "settled_at": p.settled_at.isoformat() if p.settled_at else None,
                "amount": p.amount,
                "payment_state": p.payment_state,
                "reversal_of": p.reversal_of,
            }
            for p in payments
        ],
        "refunds": [
            {
                "refund_ref": r.refund_ref,
                "requested_at": r.requested_at.isoformat() if r.requested_at else None,
                "settled_at": r.settled_at.isoformat() if r.settled_at else None,
                "amount": r.amount,
                "refund_state": r.refund_state,
            }
            for r in refunds
        ],
    }


def render_order_text(detail: dict) -> str:
    lines = [f"订单 {detail['order_ref']} 追溯明细"]
    session = detail["session"] or {}
    lines.append(
        f"  付款人 {detail['household_ref']}，课程 {detail['course_ref']}"
        f"（版本 {detail['course_version'] or '未知'}，{detail['course_units']} 单位），"
        f"状态 {detail['state']}"
    )
    lines.append(
        f"  下单 {detail['ordered_at']}，订单额 {_money(detail['gross_amount'] or 0)}，"
        f"类别 {CLASS_LABELS[detail['class']]}，"
        f"新签贡献 {_money(detail['signing_contribution'])}"
    )
    lines.append(
        f"  场次 {session.get('session_ref', '未知')}"
        f"（{session.get('sales_method', '未知')}，内容版本 "
        f"{session.get('content_version', '未知')}）"
    )
    lines.append(
        f"  父单链 {' <- '.join(detail['parents']) or '无'}；"
        f"子单 {', '.join(detail['children']) or '无'}"
    )
    for payment in detail["payments"]:
        reversal = f"，冲正 {payment['reversal_of']}" if payment["reversal_of"] else ""
        lines.append(
            f"  付款 {payment['payment_ref']}：{payment['settled_at'] or '未结算'} "
            f"{_money(payment['amount'] or 0)}（{payment['payment_state']}{reversal}）"
        )
    for refund in detail["refunds"]:
        lines.append(
            f"  退款 {refund['refund_ref']}：申请 {refund['requested_at'] or '未知'}，"
            f"结算 {refund['settled_at'] or '未结算'} "
            f"{_money(refund['amount'] or 0)}（{refund['refund_state']}）"
        )
    return "\n".join(lines)

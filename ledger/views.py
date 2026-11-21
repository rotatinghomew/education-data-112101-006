"""核算口径：销售表现、资金变化、服务履约三种相互独立的视角。

- 销售表现按订单创建时间（ordered_at）归入销售期，区分新购、升级、续单；
  升级单只计相对父单的增量，同一笔原有服务不在新签总额里重复出现。
- 资金变化按实际结算时间（settled_at）归期；已完成的付款冲正与退款计入
  发生期间；尚未结算的退款与仍有争议的款项单独列示，不计入现金流。
- 服务履约以课程版本的服务单位计量；部分退款不抹掉仍然有效的服务，
  升级链中的服务由最新订单承接。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .loader import Dataset
from .model import CourseVersion, Order, Refund

NEW = "new"
UPGRADE = "upgrade"
RENEWAL = "renewal"
CLASS_LABELS = {NEW: "新购", UPGRADE: "升级", RENEWAL: "续单"}
DISPUTED = "disputed"


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def quarter_key(day: date) -> str:
    return f"{day.year:04d}Q{(day.month - 1) // 3 + 1}"


def quarter_of_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year:04d}Q{(mon - 1) // 3 + 1}"


# ---------------------------------------------------------------- 销售视角

def classify_order(order: Order, orders_by_ref: dict[str, Order]) -> str:
    """按父子关系与课程区分新购、升级、续单。

    无父单为新购；父单课程相同为续单（同一课程的新服务周期）；
    父单课程不同为升级（例如体验课升级为正式课程）。
    """
    if not order.parent_order:
        return NEW
    parent = orders_by_ref.get(order.parent_order)
    if parent is not None and parent.course_ref == order.course_ref:
        return RENEWAL
    return UPGRADE


def signing_contribution(order: Order, orders_by_ref: dict[str, Order]) -> int:
    """订单对新签总额（去重后）的贡献。

    升级单只计相对父单金额的增量，避免同一笔原有服务随子单全额重复计入；
    新购与续单按订单全额计入（续单对应新的服务周期）。
    """
    gross = order.gross_amount or 0
    if classify_order(order, orders_by_ref) != UPGRADE:
        return gross
    parent = orders_by_ref.get(order.parent_order)
    parent_gross = (parent.gross_amount or 0) if parent else 0
    return max(0, gross - parent_gross)


# ---------------------------------------------------------------- 资金视角

@dataclass(frozen=True)
class CashEvent:
    """一笔已发生的资金变动；amount 有符号：收款为正，冲正与退款为负。"""

    occurred_at: date
    kind: str  # payment / reversal / refund
    amount: int
    order_ref: str
    source_ref: str
    linked_ref: str | None
    note: str


def refund_state_as_of(refund: Refund, as_of: date | None) -> str | None:
    """按口径日期还原退款状态。

    返回 None 表示该口径日此退款尚不可见；结算日之前为 pending，
    结算日起为 settled。as_of 为 None 时按资料中记录的当前状态。
    """
    if as_of is None:
        return refund.refund_state
    if refund.requested_at and refund.requested_at > as_of:
        return None
    if refund.settled_at and refund.settled_at <= as_of:
        return "settled"
    return "pending"


def cash_events(ds: Dataset, as_of: date | None = None) -> list[CashEvent]:
    """汇总截至口径日已实际发生的资金变动（不含未结算退款）。"""
    events: list[CashEvent] = []
    for payment in ds.payments:
        if payment.settled_at is None:
            continue
        if as_of is not None and payment.settled_at > as_of:
            continue
        if payment.is_reversal:
            events.append(
                CashEvent(
                    occurred_at=payment.settled_at,
                    kind="reversal",
                    amount=-(payment.amount or 0),
                    order_ref=payment.order_ref,
                    source_ref=payment.payment_ref,
                    linked_ref=payment.reversal_of,
                    note=f"冲正 {payment.reversal_of}",
                )
            )
        else:
            events.append(
                CashEvent(
                    occurred_at=payment.settled_at,
                    kind="payment",
                    amount=payment.amount or 0,
                    order_ref=payment.order_ref,
                    source_ref=payment.payment_ref,
                    linked_ref=None,
                    note="收款结算",
                )
            )
    for refund in ds.refunds:
        if refund_state_as_of(refund, as_of) != "settled" or refund.settled_at is None:
            continue
        events.append(
            CashEvent(
                occurred_at=refund.settled_at,
                kind="refund",
                amount=-(refund.amount or 0),
                order_ref=refund.order_ref,
                source_ref=refund.refund_ref,
                linked_ref=None,
                note="退款结算",
            )
        )
    events.sort(key=lambda e: (e.occurred_at, e.source_ref))
    return events


def pending_refunds(ds: Dataset, as_of: date | None = None) -> list[Refund]:
    """截至口径日尚未结算的退款：单独列示，不计入已发生现金流。"""
    return [r for r in ds.refunds if refund_state_as_of(r, as_of) == "pending"]


# ---------------------------------------------------------------- 履约视角

@dataclass(frozen=True)
class FulfillmentLine:
    order_ref: str
    course_ref: str
    version: str | None
    units: int
    state: str
    flag: str | None


def resolve_course_version(
    ds: Dataset, course_ref: str, day: date | None
) -> CourseVersion | None:
    """按下单日匹配当时有效的课程版本；无精确匹配时取不晚于下单日的最新版本。"""
    candidates = [v for v in ds.course_versions if v.course_ref == course_ref]
    for version in candidates:
        if version.covers(day):
            return version
    past = [v for v in candidates if v.valid_from and day and v.valid_from <= day]
    if past:
        return max(past, key=lambda v: v.valid_from)
    return None


def entitlement_lines(
    ds: Dataset, orders: list[Order] | None = None
) -> list[FulfillmentLine]:
    """有效服务单位明细。

    升级链中已被子单取代的父单不重复计单位，服务由最新订单承接；
    部分退款不影响仍然有效的服务单位；争议订单保留但加以标记。
    """
    lines: list[FulfillmentLine] = []
    for order in orders if orders is not None else ds.orders:
        if order.state == "upgraded" and ds.children_by_parent.get(order.order_ref):
            continue  # 服务由子单承接
        version = resolve_course_version(ds, order.course_ref, order.ordered_at)
        units = (version.units or 0) if version else 0
        flag = None
        if order.state == DISPUTED:
            flag = "争议中，暂按有效计"
        elif order.state == "upgraded":
            flag = "标记已升级但无子单，暂按有效计"
        lines.append(
            FulfillmentLine(
                order_ref=order.order_ref,
                course_ref=order.course_ref,
                version=version.version if version else None,
                units=units,
                state=order.state,
                flag=flag,
            )
        )
    return lines

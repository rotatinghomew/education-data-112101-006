"""读取 data/ 下的合成资料，建立索引并做一致性检查。

一致性检查发现的问题记入 Dataset.warnings，只提示、不阻断核算；
这些提示随报告一并输出，保证合成资料中的异常关系可以被追溯。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .model import (
    CourseVersion,
    Order,
    Payment,
    Refund,
    Session,
    parse_date,
    parse_int,
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


@dataclass
class Dataset:
    orders: list[Order]
    payments: list[Payment]
    refunds: list[Refund]
    sessions: list[Session]
    course_versions: list[CourseVersion]
    warnings: list[str] = field(default_factory=list)
    orders_by_ref: dict[str, Order] = field(init=False, repr=False)
    payments_by_ref: dict[str, Payment] = field(init=False, repr=False)
    sessions_by_ref: dict[str, Session] = field(init=False, repr=False)
    children_by_parent: dict[str, list[Order]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.orders_by_ref = {o.order_ref: o for o in self.orders}
        self.payments_by_ref = {p.payment_ref: p for p in self.payments}
        self.sessions_by_ref = {s.session_ref: s for s in self.sessions}
        self.children_by_parent = {}
        for order in self.orders:
            if order.parent_order:
                self.children_by_parent.setdefault(order.parent_order, []).append(order)

    def sales_method(self, order_ref: str) -> str:
        order = self.orders_by_ref.get(order_ref)
        session = self.sessions_by_ref.get(order.session_ref) if order else None
        return session.sales_method if session else "未知销售方式"


def load_dataset(data_dir: str | Path) -> Dataset:
    data_dir = Path(data_dir)
    orders = [
        Order(
            order_ref=row["order_ref"],
            household_ref=row["household_ref"],
            course_ref=row["course_ref"],
            session_ref=row["session_ref"],
            ordered_at=parse_date(row["ordered_at"]),
            gross_amount=parse_int(row["gross_amount"]),
            parent_order=row["parent_order"] or None,
            state=row["state"],
        )
        for row in _read_rows(data_dir / "orders.csv")
    ]
    payments = [
        Payment(
            payment_ref=row["payment_ref"],
            order_ref=row["order_ref"],
            settled_at=parse_date(row["settled_at"]),
            amount=parse_int(row["amount"]),
            payment_state=row["payment_state"],
            reversal_of=row["reversal_of"] or None,
        )
        for row in _read_rows(data_dir / "payments.csv")
    ]
    refunds = [
        Refund(
            refund_ref=row["refund_ref"],
            order_ref=row["order_ref"],
            requested_at=parse_date(row["requested_at"]),
            settled_at=parse_date(row["settled_at"]),
            amount=parse_int(row["amount"]),
            refund_state=row["refund_state"],
            reversal_of=row["reversal_of"] or None,
        )
        for row in _read_rows(data_dir / "refunds.csv")
    ]
    sessions = [
        Session(
            session_ref=row["session_ref"],
            merchant_ref=row["merchant_ref"],
            started_at=parse_date(row["started_at"]),
            sales_method=row["sales_method"],
            content_version=row["content_version"],
        )
        for row in _read_rows(data_dir / "sessions.csv")
    ]
    course_versions = [
        CourseVersion(
            course_ref=row["course_ref"],
            version=row["version"],
            valid_from=parse_date(row["valid_from"]),
            valid_to=parse_date(row["valid_to"]),
            units=parse_int(row["units"]),
        )
        for row in _read_rows(data_dir / "course_versions.csv")
    ]
    dataset = Dataset(
        orders=orders,
        payments=payments,
        refunds=refunds,
        sessions=sessions,
        course_versions=course_versions,
    )
    _validate(dataset)
    return dataset


def _validate(ds: Dataset) -> None:
    course_refs = {v.course_ref for v in ds.course_versions}
    for order in ds.orders:
        if order.session_ref not in ds.sessions_by_ref:
            ds.warnings.append(f"订单 {order.order_ref} 引用了未知场次 {order.session_ref}")
        if order.course_ref not in course_refs:
            ds.warnings.append(f"订单 {order.order_ref} 引用了未知课程 {order.course_ref}")
        if order.parent_order:
            parent = ds.orders_by_ref.get(order.parent_order)
            if parent is None:
                ds.warnings.append(f"订单 {order.order_ref} 的父单 {order.parent_order} 不存在")
            else:
                if parent.household_ref != order.household_ref:
                    ds.warnings.append(
                        f"订单 {order.order_ref} 与父单 {parent.order_ref} 的付款人不同"
                        f"（{order.household_ref} / {parent.household_ref}）"
                    )
                if (
                    order.ordered_at
                    and parent.ordered_at
                    and order.ordered_at < parent.ordered_at
                ):
                    ds.warnings.append(
                        f"订单 {order.order_ref} 的下单时间早于其父单 {parent.order_ref}"
                    )
        if order.state == "upgraded" and not ds.children_by_parent.get(order.order_ref):
            ds.warnings.append(
                f"订单 {order.order_ref} 标记为 upgraded 但找不到子订单，履约口径暂按仍有效计"
            )
    for payment in ds.payments:
        if payment.order_ref not in ds.orders_by_ref:
            ds.warnings.append(f"付款 {payment.payment_ref} 引用了未知订单 {payment.order_ref}")
        if payment.is_reversal:
            origin = ds.payments_by_ref.get(payment.reversal_of)
            if origin is None:
                ds.warnings.append(
                    f"冲正 {payment.payment_ref} 的被冲正付款 {payment.reversal_of} 不存在"
                )
            elif origin.order_ref != payment.order_ref:
                ds.warnings.append(
                    f"冲正 {payment.payment_ref}（订单 {payment.order_ref}）与被冲正付款"
                    f" {origin.payment_ref}（订单 {origin.order_ref}）不属于同一订单"
                )
        if payment.settled_at is None:
            ds.warnings.append(f"付款 {payment.payment_ref} 缺少结算时间，未计入资金变化")
    for refund in ds.refunds:
        if refund.order_ref not in ds.orders_by_ref:
            ds.warnings.append(f"退款 {refund.refund_ref} 引用了未知订单 {refund.order_ref}")
        if refund.refund_state == "settled" and refund.settled_at is None:
            ds.warnings.append(f"退款 {refund.refund_ref} 标记为已结算但缺少结算时间")
        if refund.refund_state == "pending" and refund.settled_at is not None:
            ds.warnings.append(f"退款 {refund.refund_ref} 标记为未结算但已有结算时间")

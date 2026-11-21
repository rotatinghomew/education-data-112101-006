"""记录模型：订单、付款、退款、销售场次与课程版本。

金额以整数保存（与 CSV 文本一致），日期为 datetime.date。
空字段解析为 None，表示资料未提供，不代表数值为零或状态已否定。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


def parse_date(text: str | None) -> date | None:
    text = (text or "").strip()
    return date.fromisoformat(text) if text else None


def parse_int(text: str | None) -> int | None:
    text = (text or "").strip()
    return int(text) if text else None


@dataclass(frozen=True)
class Order:
    order_ref: str
    household_ref: str
    course_ref: str
    session_ref: str
    ordered_at: date | None
    gross_amount: int | None
    parent_order: str | None
    state: str


@dataclass(frozen=True)
class Payment:
    payment_ref: str
    order_ref: str
    settled_at: date | None
    amount: int | None
    payment_state: str
    reversal_of: str | None

    @property
    def is_reversal(self) -> bool:
        """reversal_of 非空表示本行是对另一笔付款的冲正记录。"""
        return bool(self.reversal_of)


@dataclass(frozen=True)
class Refund:
    refund_ref: str
    order_ref: str
    requested_at: date | None
    settled_at: date | None
    amount: int | None
    refund_state: str
    reversal_of: str | None


@dataclass(frozen=True)
class Session:
    session_ref: str
    merchant_ref: str
    started_at: date | None
    sales_method: str
    content_version: str


@dataclass(frozen=True)
class CourseVersion:
    course_ref: str
    version: str
    valid_from: date | None
    valid_to: date | None
    units: int | None

    def covers(self, day: date | None) -> bool:
        if day is None:
            return False
        if self.valid_from and day < self.valid_from:
            return False
        if self.valid_to and day > self.valid_to:
            return False
        return True

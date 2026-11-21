"""课程销售退款与续单的期间核算工具。

两种核对视角：
- 销售表现按订单创建时间归期，区分新购、升级、续单，升级链不重复计额；
- 资金变化按实际结算时间归期，未结算退款与争议款项单独列示。
"""
from .explain import explain, render_explain_text
from .loader import Dataset, load_dataset
from .report import build_report, order_detail, render_order_text, render_text
from .views import (
    cash_events,
    classify_order,
    entitlement_lines,
    pending_refunds,
    refund_state_as_of,
    signing_contribution,
)

__all__ = [
    "Dataset",
    "build_report",
    "cash_events",
    "classify_order",
    "entitlement_lines",
    "explain",
    "load_dataset",
    "order_detail",
    "pending_refunds",
    "refund_state_as_of",
    "render_explain_text",
    "render_order_text",
    "render_text",
    "signing_contribution",
]

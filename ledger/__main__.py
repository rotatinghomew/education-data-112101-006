"""命令行入口：

    python3 -m ledger report [--as-of YYYY-MM-DD] [--json]
    python3 -m ledger explain --from YYYY-MM-DD --to YYYY-MM-DD [--json]
    python3 -m ledger order ORDER_REF [--json]
    python3 -m ledger validate
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .explain import explain, render_explain_text
from .loader import load_dataset
from .report import build_report, order_detail, render_order_text, render_text

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _parse_date(text: str) -> date:
    return date.fromisoformat(text)


def _print(payload: dict | str, as_json: bool) -> None:
    if as_json and not isinstance(payload, str):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ledger",
        description="课程销售退款与续单的期间核算（销售视角与资金视角分列）",
    )
    parser.add_argument(
        "--data",
        default=str(DEFAULT_DATA_DIR),
        help="资料目录（默认取项目内 data/）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_report = sub.add_parser("report", help="生成经营复盘报告")
    p_report.add_argument(
        "--as-of",
        type=_parse_date,
        default=None,
        help="口径日期：按当时可见的交易状态还原报告",
    )
    p_report.add_argument("--json", action="store_true", help="输出 JSON")

    p_explain = sub.add_parser("explain", help="解释两个口径日期之间的变化来源")
    p_explain.add_argument("--from", dest="d_from", type=_parse_date, required=True)
    p_explain.add_argument("--to", dest="d_to", type=_parse_date, required=True)
    p_explain.add_argument("--json", action="store_true", help="输出 JSON")

    p_order = sub.add_parser("order", help="查看单笔订单的追溯明细")
    p_order.add_argument("order_ref")
    p_order.add_argument("--json", action="store_true", help="输出 JSON")

    sub.add_parser("validate", help="仅列出数据质量提示")

    args = parser.parse_args(argv)
    dataset = load_dataset(args.data)

    if args.command == "report":
        report = build_report(dataset, as_of=args.as_of)
        _print(report if args.json else render_text(report), args.json)
    elif args.command == "explain":
        result = explain(dataset, args.d_from, args.d_to)
        _print(result if args.json else render_explain_text(result), args.json)
    elif args.command == "order":
        if args.order_ref not in dataset.orders_by_ref:
            print(f"找不到订单 {args.order_ref}", file=sys.stderr)
            return 1
        detail = order_detail(dataset, args.order_ref)
        _print(detail if args.json else render_order_text(detail), args.json)
    elif args.command == "validate":
        if dataset.warnings:
            for warning in dataset.warnings:
                print(f"- {warning}")
        else:
            print("未发现数据质量问题。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

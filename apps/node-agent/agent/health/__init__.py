from __future__ import annotations

"""Reconcile 对账：把 desired vs observed 转成 ReconciliationReport。"""

from .reconcile import build_reconciliation_report


__all__ = ["build_reconciliation_report"]

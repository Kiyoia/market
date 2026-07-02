"""ClickHouse 云端测试表辅助函数"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from db import execute


@dataclass(frozen=True)
class ClickHouseTestTables:
    """本次云端测试使用的唯一测试表名"""

    categories: str
    products: str
    daily_prices: str
    results: str


def make_test_tables() -> ClickHouseTestTables:
    """生成唯一的 test_ 测试表名"""
    suffix = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
    return ClickHouseTestTables(
        categories=f"test_oss_categories_{suffix}",
        products=f"test_oss_products_{suffix}",
        daily_prices=f"test_oss_daily_prices_{suffix}",
        results=f"test_price_index_results_{suffix}",
    )


def assert_test_table_name(table_name: str) -> None:
    """确认表名只能是 test_ 开头的安全测试表"""
    if not re.fullmatch(r"test_[A-Za-z0-9_]+", table_name):
        raise AssertionError(f"云端测试表名不安全：{table_name}")


def iter_test_table_names(tables: ClickHouseTestTables) -> list[str]:
    """返回本次测试涉及的全部表名"""
    return [
        tables.categories,
        tables.products,
        tables.daily_prices,
        tables.results,
    ]


def assert_all_test_tables(tables: ClickHouseTestTables) -> None:
    """确认所有表名都只能指向 test_ 测试表"""
    for table_name in iter_test_table_names(tables):
        assert_test_table_name(table_name)


def drop_test_tables(tables: ClickHouseTestTables) -> None:
    """删除本次云端测试创建的 test_ 表"""
    assert_all_test_tables(tables)
    for table_name in reversed(iter_test_table_names(tables)):
        execute(f"DROP TABLE IF EXISTS {table_name}")

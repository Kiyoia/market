"""可选云端 ClickHouse 测试，只操作 test_ 表"""

import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from config import settings
from data_loader import DataLoader
from db import query_df
from pipeline import calculate_index_result, persist_index_result
from tests.support.clickhouse_test_tables import (
    assert_all_test_tables,
    assert_test_table_name,
    drop_test_tables,
    make_test_tables,
)
from tests.support.fixtures import create_standard_data_files, loose_anomaly_params


@unittest.skipUnless(
    os.environ.get("MARKET_CLOUD_TESTS") == "1",
    "默认跳过云端 ClickHouse 测试；设置 MARKET_CLOUD_TESTS=1 后执行",
)
class TestCloudClickHouseWithTestTables(unittest.TestCase):
    """验证云端增删查改只发生在 test_ 测试表"""

    def setUp(self):
        try:
            settings.clickhouse.validate()
        except ValueError as exc:
            raise AssertionError(f"云端测试缺少 ClickHouse 配置：{exc}") from exc

        self.test_dir = tempfile.mkdtemp()
        self.data_dir = f"{self.test_dir}/data"
        create_standard_data_files(self.data_dir)
        self.tables = make_test_tables()
        assert_all_test_tables(self.tables)

        self.test_settings = SimpleNamespace(
            data_dir=self.data_dir,
            base_date="2025-05-17",
            anomaly_params=loose_anomaly_params(),
            safety=SimpleNamespace(db_write_enabled=True, schema_reset_enabled=True),
        )

    def tearDown(self):
        try:
            drop_test_tables(self.tables)
        finally:
            shutil.rmtree(self.test_dir, ignore_errors=True)

    @contextmanager
    def patched_test_tables(self):
        """把业务模块表名临时指向本次 test_ 表"""
        with ExitStack() as stack:
            stack.enter_context(patch("data_loader.TABLE_CATEGORIES", self.tables.categories))
            stack.enter_context(patch("data_loader.TABLE_PRODUCTS", self.tables.products))
            stack.enter_context(patch("data_loader.TABLE_DAILY_PRICES", self.tables.daily_prices))
            stack.enter_context(patch("data_loader.settings", self.test_settings))

            stack.enter_context(patch("data_cleaner.TABLE_CATEGORIES", self.tables.categories))
            stack.enter_context(patch("data_cleaner.TABLE_PRODUCTS", self.tables.products))
            stack.enter_context(patch("data_cleaner.TABLE_DAILY_PRICES", self.tables.daily_prices))

            stack.enter_context(patch("db.TABLE_CATEGORIES", self.tables.categories))
            stack.enter_context(patch("db.TABLE_PRODUCTS", self.tables.products))
            stack.enter_context(patch("db.TABLE_DAILY_PRICES", self.tables.daily_prices))
            stack.enter_context(patch("db.TABLE_PRICE_INDEX_RESULTS", self.tables.results))

            stack.enter_context(patch("pipeline.TABLE_PRICE_INDEX_RESULTS", self.tables.results))
            stack.enter_context(patch("pipeline.settings", self.test_settings))
            yield

    def table_count(self, table_name: str) -> int:
        """查询 test_ 表记录数"""
        assert_test_table_name(table_name)
        result = query_df(f"SELECT count() AS count FROM {table_name}")
        return int(result["count"].iloc[0])

    def test_data_loader_creates_inserts_and_reads_only_test_tables(self):
        with self.patched_test_tables():
            DataLoader(self.data_dir, force_reload=True).load_all()

            self.assertEqual(self.table_count(self.tables.categories), 3)
            self.assertEqual(self.table_count(self.tables.products), 5)
            self.assertEqual(self.table_count(self.tables.daily_prices), 15)

    def test_clean_calculate_and_persist_only_use_test_tables(self):
        with self.patched_test_tables():
            DataLoader(self.data_dir, force_reload=True).load_all()

            result_df = calculate_index_result()
            persist_index_result(result_df)

            self.assertFalse(result_df.empty)
            self.assertGreater(self.table_count(self.tables.results), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

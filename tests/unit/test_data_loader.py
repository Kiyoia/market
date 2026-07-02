"""数据加载模块单元测试"""

import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from data_loader import DataLoader
from tests.support.fixtures import create_standard_data_files


class TestDataLoader(unittest.TestCase):
    """按图片说明验证分类、商品和日价格加载"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.data_dir = f"{self.test_dir}/data"
        create_standard_data_files(self.data_dir)
        self.loader = DataLoader(self.data_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_categories_contains_expected_columns_and_rows(self):
        categories = self.loader._load_categories()

        self.assertEqual(len(categories), 3)
        self.assertEqual(
            set(["category_id", "category_name", "hierarchy", "weight", "parent"]),
            set(categories.columns),
        )
        self.assertEqual(categories["category_name"].tolist(), ["粮食", "淀粉", "食用油"])

    def test_load_products_contains_expected_fields(self):
        products = self.loader._load_products()

        self.assertEqual(len(products), 5)
        self.assertIn("product_id", products.columns)
        self.assertIn("category_id", products.columns)
        self.assertEqual(products.loc[products["product_id"] == "1004", "name"].iloc[0], "花生油")

    def test_load_daily_prices_parses_dates_and_links_categories(self):
        products = self.loader._load_products()
        daily = self.loader._load_daily_prices(products)

        self.assertEqual(len(daily), 15)
        self.assertEqual(daily["date"].nunique(), 3)
        self.assertIn("category_id", daily.columns)
        self.assertEqual(
            daily.loc[daily["product_id"] == "1003", "category_id"].iloc[0],
            "1101020000",
        )

    def test_load_all_uploads_with_mocked_clickhouse(self):
        writable_settings = SimpleNamespace(
            safety=SimpleNamespace(db_write_enabled=True, schema_reset_enabled=False)
        )

        with patch("data_loader.settings", writable_settings), \
                patch("data_loader.insert_df") as insert_df, \
                patch("data_loader.execute") as execute:
            categories, products, daily = self.loader.load_all()

        self.assertEqual(len(categories), 3)
        self.assertEqual(len(products), 5)
        self.assertEqual(len(daily), 15)
        self.assertEqual(insert_df.call_count, 3)
        execute.assert_not_called()

    def test_upload_rejected_when_readonly(self):
        categories = self.loader._load_categories()
        products = self.loader._load_products()
        daily = self.loader._load_daily_prices(products)

        with self.assertRaises(PermissionError):
            self.loader._upload_to_clickhouse(categories, products, daily)

    def test_force_reload_requires_schema_reset_enabled(self):
        writable_settings = SimpleNamespace(
            safety=SimpleNamespace(db_write_enabled=True, schema_reset_enabled=False)
        )
        loader = DataLoader(self.data_dir, force_reload=True)
        categories = loader._load_categories()
        products = loader._load_products()
        daily = loader._load_daily_prices(products)

        with patch("data_loader.settings", writable_settings):
            with self.assertRaises(PermissionError):
                loader._upload_to_clickhouse(categories, products, daily)


if __name__ == "__main__":
    unittest.main(verbosity=2)

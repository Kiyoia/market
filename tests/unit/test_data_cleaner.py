"""数据清洗模块单元测试"""

import unittest
from unittest.mock import patch

from config import settings
from data_cleaner import DataCleaner
from tests.support.fixtures import (
    category_daily_df,
    cleaned_price_records_df,
    dirty_price_records_df,
)


class TestDataCleaner(unittest.TestCase):
    """按图片说明验证异常剔除和分类日度聚合"""

    def setUp(self):
        self.cleaner = DataCleaner(settings.anomaly_params)

    def test_clean_removes_high_low_and_null_prices(self):
        dirty_df = dirty_price_records_df()
        expected = cleaned_price_records_df()

        with patch("data_cleaner.get_date_range", return_value=("2025-05-17", "2025-05-17")), \
                patch("data_cleaner.get_daily_count", return_value=len(dirty_df)), \
                patch("data_cleaner.query_df", return_value=expected) as query_df:
            result = self.cleaner.clean()

        self.assertEqual(len(result), 2)
        self.assertNotIn(999.99, result["price"].tolist())
        self.assertNotIn(3.33, result["price"].tolist())
        self.assertFalse(result["price"].isna().any())

        sql = query_df.call_args.args[0]
        self.assertIn("d.price IS NOT NULL", sql)
        self.assertIn("is_anomaly", sql)

    def test_compute_category_daily_returns_weighted_statistics(self):
        expected = category_daily_df()

        with patch("data_cleaner.get_date_range", return_value=("2025-05-17", "2025-05-17")), \
                patch("data_cleaner.get_daily_count", return_value=2), \
                patch("data_cleaner.query_df", return_value=expected):
            result = self.cleaner.compute_category_daily()

        self.assertEqual(len(result), 1)
        self.assertEqual(result["category_name"].iloc[0], "粮食")
        self.assertIn("weighted_avg_price", result.columns)
        self.assertIn("sales_qty", result.columns)
        self.assertAlmostEqual(result["weighted_avg_price"].iloc[0], 3400.0 / 300.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

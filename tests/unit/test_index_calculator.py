"""指数计算模块单元测试"""

import unittest
from unittest.mock import patch

import pandas as pd

from config import settings
from index_calculator import IndexCalculator
from tests.support.fixtures import category_daily_for_index_df, index_categories_df


class TestIndexCalculator(unittest.TestCase):
    """按图片说明验证基期、链式指数和全局指数"""

    def setUp(self):
        self.calculator = IndexCalculator(settings.base_date)

    def test_chain_price_index_starts_from_base_100(self):
        category_daily = category_daily_for_index_df()

        result = self.calculator.compute_chain_price_index(category_daily)
        grain = result[result["category_id"] == "1101010000"].sort_values("date")

        self.assertEqual(len(grain), 3)
        self.assertEqual(grain["index_value"].iloc[0], 100.0)
        self.assertAlmostEqual(grain["index_value"].iloc[1], 110.0)
        self.assertAlmostEqual(grain["index_value"].iloc[2], 105.0)

    def test_aggregated_index_uses_category_weights(self):
        index_df = pd.DataFrame({
            "date": pd.to_datetime(["2025-05-17", "2025-05-17"]),
            "category_id": ["1101010000", "1101020000"],
            "category_name": ["粮食", "淀粉"],
            "index_value": [100.0, 120.0],
            "weighted_price": [10.0, 8.0],
            "fisher": [100.0, 120.0],
        })

        with patch("index_calculator.get_categories", return_value=index_categories_df()):
            result = self.calculator.compute_aggregated_index(index_df, level="global")

        self.assertEqual(len(result), 1)
        self.assertIn("global_index", result.columns)
        self.assertAlmostEqual(result["global_index"].iloc[0], 105.0)

    def test_missing_date_does_not_create_extra_index_record(self):
        category_daily = category_daily_for_index_df()
        category_daily = category_daily[
            ~(
                (category_daily["category_id"] == "1101020000")
                & (category_daily["date"] == pd.Timestamp("2025-05-18"))
            )
        ]

        result = self.calculator.compute_chain_price_index(category_daily)
        starch = result[result["category_id"] == "1101020000"]

        self.assertEqual(len(starch), 2)
        self.assertNotIn(pd.Timestamp("2025-05-18"), starch["date"].tolist())

    def test_weighted_avg_price_maps_products_to_second_level_categories(self):
        cleaned_df = pd.DataFrame({
            "date": pd.to_datetime(["2025-05-17", "2025-05-17"]),
            "product_id": ["1001", "1002"],
            "category_id": ["1101010000", "1101010000"],
            "price": [10.0, 12.0],
            "weight": [0.10, 0.20],
        })

        with patch("index_calculator.get_categories", return_value=index_categories_df()):
            result = self.calculator.compute_weighted_avg_price(cleaned_df)

        self.assertEqual(len(result), 1)
        self.assertEqual(result["category_name"].iloc[0], "粮食")
        self.assertAlmostEqual(result["weighted_avg_price"].iloc[0], 3400.0 / 300.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

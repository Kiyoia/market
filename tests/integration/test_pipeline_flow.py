"""本地集成流程测试"""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

import api
import main
from api import app
from config import settings
from data_cleaner import DataCleaner
from data_loader import DataLoader
from index_calculator import IndexCalculator
from pipeline import calculate_index_result, persist_index_result, run_index_pipeline, save_index_files
from tests.support.fixtures import (
    category_daily_for_index_df,
    create_standard_data_files,
    index_categories_df,
)
from visualizer import Visualizer


def sample_result_df():
    """构造固定结果契约的价格指数结果"""
    return pd.DataFrame({
        "date": pd.to_datetime(["2025-05-17", "2025-05-18"]).date,
        "category_id": [1101010000, 1101010000],
        "category_name": ["粮食", "粮食"],
        "index_value": [100.0, 110.0],
        "weighted_price": [10.0, 11.0],
        "fisher": [100.0, 110.0],
        "global_index": [100.0, 110.0],
        "created_at": pd.to_datetime(["2025-05-17 00:00:00", "2025-05-18 00:00:00"]),
    })


class TestLocalPipelineFlow(unittest.TestCase):
    """按生产顺序验证本地集成流程"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.data_dir = f"{self.test_dir}/data"
        create_standard_data_files(self.data_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_clean_calculate_and_visualize_without_clickhouse(self):
        loader = DataLoader(self.data_dir)
        products = loader._load_products()
        daily = loader._load_daily_prices(products)

        cleaner = DataCleaner(settings.anomaly_params)
        calculator = IndexCalculator("2025-05-17")
        with patch("data_cleaner.get_date_range", return_value=("2025-05-17", "2025-05-19")), \
                patch("data_cleaner.get_daily_count", return_value=len(daily)), \
                patch("data_cleaner.query_df", return_value=category_daily_for_index_df()), \
                patch("index_calculator.get_categories", return_value=index_categories_df()), \
                patch("visualizer.get_top_categories", return_value=["粮食", "淀粉"]):
            category_daily = cleaner.compute_category_daily()
            index_df = calculator.compute_chain_price_index(category_daily)
            aggregated_df = calculator.compute_aggregated_index(index_df, level="global")
            chart_df = index_df.merge(aggregated_df, on="date", how="left")
            Visualizer().plot_price_index(chart_df, show=False)

        self.assertEqual(len(products), 5)
        self.assertEqual(daily["date"].nunique(), 3)
        self.assertFalse(category_daily.empty)
        self.assertFalse(index_df.empty)
        self.assertFalse(aggregated_df.empty)

    def test_save_index_files_writes_csv_and_requests_chart(self):
        test_settings = SimpleNamespace(data_dir=self.data_dir, base_date="2025-05-17")

        with patch("pipeline.settings", test_settings), \
                patch("pipeline.Visualizer") as visualizer_class:
            result = save_index_files(sample_result_df(), save_chart=True)

        csv_path = os.path.join(self.data_dir, "price_index_results.csv")
        self.assertTrue(os.path.exists(csv_path))
        self.assertEqual(len(pd.read_csv(csv_path)), 2)
        self.assertEqual(len(result), 2)
        visualizer_class.return_value.plot_price_index.assert_called_once()
        self.assertEqual(
            visualizer_class.return_value.plot_price_index.call_args.kwargs["save_path"],
            os.path.join(self.data_dir, "price_index_trend.png"),
        )

    def test_calculate_index_result_is_readonly(self):
        cleaner = SimpleNamespace(compute_category_daily=category_daily_for_index_df)

        with patch("pipeline.DataCleaner", return_value=cleaner), \
                patch("index_calculator.get_categories", return_value=index_categories_df()), \
                patch("pipeline.insert_df") as insert_df, \
                patch("pipeline.execute") as execute:
            result = calculate_index_result()

        self.assertFalse(result.empty)
        insert_df.assert_not_called()
        execute.assert_not_called()

    def test_main_pipeline_saves_local_files_without_db_write(self):
        with patch("pipeline.calculate_index_result", return_value=sample_result_df()), \
                patch("pipeline.save_index_files") as save_files, \
                patch("pipeline.insert_df") as insert_df, \
                patch("pipeline.execute") as execute:
            exit_code = main.run_pipeline()

        self.assertEqual(exit_code, 0)
        save_files.assert_called_once()
        insert_df.assert_not_called()
        execute.assert_not_called()

    def test_persist_index_result_rejects_clickhouse_write_in_readonly_mode(self):
        with patch("pipeline.insert_df") as insert_df, \
                patch("pipeline.execute") as execute:
            with self.assertRaises(PermissionError):
                persist_index_result(sample_result_df())

        insert_df.assert_not_called()
        execute.assert_not_called()

    def test_run_index_pipeline_rejects_persist_in_readonly_mode(self):
        with patch("pipeline.calculate_index_result", return_value=sample_result_df()), \
                patch("pipeline.save_index_files") as save_files, \
                patch("pipeline.insert_df") as insert_df, \
                patch("pipeline.execute") as execute:
            with self.assertRaises(PermissionError):
                run_index_pipeline(save_chart=False)

        save_files.assert_called_once()
        self.assertTrue(save_files.call_args.args[0].equals(sample_result_df()))
        self.assertFalse(save_files.call_args.kwargs["save_chart"])
        insert_df.assert_not_called()
        execute.assert_not_called()

    def test_write_api_returns_403_by_default(self):
        client = TestClient(app)

        data_load = client.post("/api/data/load", json={"force_reload": False})
        calculate = client.post("/api/index/calculate")
        refresh = client.post("/api/index/refresh")

        self.assertEqual(data_load.status_code, 403)
        self.assertEqual(calculate.status_code, 403)
        self.assertEqual(refresh.status_code, 403)

    def test_force_reload_requires_schema_reset_switch(self):
        writable_settings = SimpleNamespace(
            data_dir="./data",
            safety=SimpleNamespace(db_write_enabled=True, schema_reset_enabled=False),
        )
        client = TestClient(app)

        with patch.object(api, "settings", writable_settings):
            response = client.post("/api/data/load", json={"force_reload": True})

        self.assertEqual(response.status_code, 403)
        self.assertIn("force_reload", response.json()["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

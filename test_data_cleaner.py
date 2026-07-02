# test_data_cleaner.py
"""数据清洗模块单元测试 - 使用直接SQL插入"""

import unittest
import pandas as pd
import os
import tempfile
import shutil
import sys
import logging

# 禁用日志
logging.disable(logging.CRITICAL)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_loader import DataLoader
from data_cleaner import DataCleaner
from config import CLICKHOUSE_CONFIG, ANOMALY_PARAMS
from db import get_client, execute, TABLE_CATEGORIES, TABLE_PRODUCTS, TABLE_DAILY_PRICES


class TestDataCleaner(unittest.TestCase):
    """数据清洗器测试 - 使用直接SQL插入"""

    @classmethod
    def setUpClass(cls):
        """类级别初始化"""
        try:
            cls.client = get_client()
            cls.client.execute("SELECT 1")
            cls.connection_ok = True
            print("✓ ClickHouse连接成功")
        except Exception as e:
            print(f"✗ ClickHouse连接失败: {e}")
            cls.connection_ok = False

    @classmethod
    def tearDownClass(cls):
        """清理测试数据"""
        if cls.connection_ok:
            try:
                cls.client.execute(f"DROP TABLE IF EXISTS {TABLE_CATEGORIES}")
                cls.client.execute(f"DROP TABLE IF EXISTS {TABLE_PRODUCTS}")
                cls.client.execute(f"DROP TABLE IF EXISTS {TABLE_DAILY_PRICES}")
            except:
                pass

    def setUp(self):
        """测试前置准备"""
        if not self.connection_ok:
            self.skipTest("ClickHouse连接不可用")

        self.test_dir = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.test_dir, 'data')
        os.makedirs(self.data_dir)

        self._create_test_data_with_anomalies()

        # 使用直接SQL插入，绕过DataLoader的插入问题
        self._insert_data_directly()

        self.cleaner = DataCleaner(ANOMALY_PARAMS)

    def tearDown(self):
        """测试后清理"""
        if hasattr(self, 'test_dir') and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_test_data_with_anomalies(self):
        """创建包含异常值的测试数据CSV文件"""
        # 分类数据
        categories_df = pd.DataFrame({
            'category_id': ['1101010000', '1101020000'],
            'category_name': ['粮食', '淀粉'],
            'hierarchy': [2, 2],
            'weight': [0.0075, 0.0070],
            'parent': ['1101000000', '1101000000']
        })
        categories_df.to_csv(f'{self.data_dir}/categories.csv', index=False, encoding='utf-8')

        # 商品数据
        products_df = pd.DataFrame({
            'product_id': ['1001', '1002', '1003', '1004', '1005', '1006'],
            'category_id': ['1101010000', '1101010000', '1101010000', '1101010000', '1101010000', '1101020000'],
            'name': ['大米_1', '大米_2', '大米_3', '大米_4', '大米_5', '淀粉_1'],
            'weight': [0.01, 0.02, 0.015, 0.025, 0.012, 0.018],
            'price': [3.06, 3.10, 2.78, 4.53, 3.33, 2.80]
        })
        products_df.to_csv(f'{self.data_dir}/products.csv', index=False, encoding='utf-8')

        # 日价格数据（包含异常值）
        daily_dir = os.path.join(self.data_dir, 'daily_price')
        os.makedirs(daily_dir)

        # 第一天：包含异常值
        daily_df = pd.DataFrame({
            'product_id': ['1001', '1002', '1003', '1004', '1005', '1006'],
            'price': [3.06, 3.10, 2.78, 999.99, -3.33, 2.80],  # 1004异常高, 1005负数
            'sales_qty': [100, 80, 120, 90, 70, 60]
        })
        daily_df.to_csv(f'{daily_dir}/daily_prices_20250517.csv', index=False, encoding='utf-8')

        # 第二天：包含异常值
        daily_df = pd.DataFrame({
            'product_id': ['1001', '1002', '1003', '1004', '1005', '1006'],
            'price': [3.10, 3.15, 2.80, 4.60, 100.00, 2.85],  # 1005异常高
            'sales_qty': [110, 85, 115, 95, 75, 65]
        })
        daily_df.to_csv(f'{daily_dir}/daily_prices_20250518.csv', index=False, encoding='utf-8')

    def _insert_data_directly(self):
        """直接使用SQL插入数据到ClickHouse"""
        client = self.client

        # 创建表（先删除再创建）
        for table in [TABLE_CATEGORIES, TABLE_PRODUCTS, TABLE_DAILY_PRICES]:
            try:
                client.execute(f"DROP TABLE IF EXISTS {table}")
            except:
                pass

        # 创建分类表
        client.execute(f"""
            CREATE TABLE {TABLE_CATEGORIES} (
                category String,
                category_id UInt64,
                hierarchy UInt8,
                weight Float64,
                price String,
                parent String
            ) ENGINE = MergeTree()
            ORDER BY category_id
        """)

        # 创建商品表
        client.execute(f"""
            CREATE TABLE {TABLE_PRODUCTS} (
                product_id UInt64,
                category_id UInt64,
                name String,
                weight Float64,
                price Float64,
                change_count Int32
            ) ENGINE = MergeTree()
            ORDER BY product_id
        """)

        # 创建日价格表
        client.execute(f"""
            CREATE TABLE {TABLE_DAILY_PRICES} (
                product_id UInt64,
                category_id UInt64,
                name String,
                price Float64,
                change_date Date
            ) ENGINE = MergeTree()
            PARTITION BY change_date
            ORDER BY (change_date, product_id)
        """)

        # 插入分类数据
        client.execute(f"""
            INSERT INTO {TABLE_CATEGORIES} (category, category_id, hierarchy, weight, price, parent)
            VALUES 
                ('粮食', 1101010000, 2, 0.0075, 'null', '1101000000'),
                ('淀粉', 1101020000, 2, 0.0070, 'null', '1101000000')
        """)

        # 插入商品数据
        client.execute(f"""
            INSERT INTO {TABLE_PRODUCTS} (product_id, category_id, name, weight, price, change_count)
            VALUES 
                (1001, 1101010000, '大米_1', 0.01, 3.06, 0),
                (1002, 1101010000, '大米_2', 0.02, 3.10, 0),
                (1003, 1101010000, '大米_3', 0.015, 2.78, 0),
                (1004, 1101010000, '大米_4', 0.025, 4.53, 0),
                (1005, 1101010000, '大米_5', 0.012, 3.33, 0),
                (1006, 1101020000, '淀粉_1', 0.018, 2.80, 0)
        """)

        # 插入日价格数据
        client.execute(f"""
            INSERT INTO {TABLE_DAILY_PRICES} (product_id, category_id, name, price, change_date)
            VALUES 
                (1001, 1101010000, '大米_1', 3.06, '2025-05-17'),
                (1002, 1101010000, '大米_2', 3.10, '2025-05-17'),
                (1003, 1101010000, '大米_3', 2.78, '2025-05-17'),
                (1004, 1101010000, '大米_4', 999.99, '2025-05-17'),
                (1005, 1101010000, '大米_5', -3.33, '2025-05-17'),
                (1006, 1101020000, '淀粉_1', 2.80, '2025-05-17'),
                (1001, 1101010000, '大米_1', 3.10, '2025-05-18'),
                (1002, 1101010000, '大米_2', 3.15, '2025-05-18'),
                (1003, 1101010000, '大米_3', 2.80, '2025-05-18'),
                (1004, 1101010000, '大米_4', 4.60, '2025-05-18'),
                (1005, 1101010000, '大米_5', 100.00, '2025-05-18'),
                (1006, 1101020000, '淀粉_1', 2.85, '2025-05-18')
        """)

        print("  ✓ 直接SQL插入成功")

    def test_anomaly_detection(self):
        """测试异常值检测"""
        cleaned_df = self.cleaner.clean()
        self.assertIsNotNone(cleaned_df)

        # 验证异常值被剔除
        # 1004价格为999.99应被剔除
        p004_data = cleaned_df[cleaned_df['product_id'] == '1004']
        self.assertTrue(len(p004_data) == 0 or (p004_data['price'] < 100).all())

        # 1005负数价格应被剔除
        p005_data = cleaned_df[cleaned_df['product_id'] == '1005']
        self.assertTrue(len(p005_data) == 0 or (p005_data['price'] > 0).all())

        print(f"  ✓ 异常值检测: 清洗后保留 {len(cleaned_df)} 条")

    def test_compute_category_daily(self):
        """测试分类日度聚合"""
        category_daily = self.cleaner.compute_category_daily()
        self.assertIsNotNone(category_daily)
        self.assertIn('weighted_avg_price', category_daily.columns)
        self.assertIn('category_name', category_daily.columns)

        # 验证聚合结果
        if not category_daily.empty:
            self.assertGreater(len(category_daily), 0)
            print(f"  ✓ 分类日度聚合: {len(category_daily)} 条")

            # 验证粮食分类的数据
            food_data = category_daily[category_daily['category_name'] == '粮食']
            if not food_data.empty:
                print(f"  ✓ 粮食分类: {len(food_data)} 条日度数据")

    def test_duplicate_removal(self):
        """测试去重功能"""
        # 先清洗
        cleaned_df = self.cleaner.clean()

        # 验证没有完全重复的记录（同日期同商品）
        if not cleaned_df.empty:
            duplicates = cleaned_df.groupby(['date', 'product_id']).size()
            self.assertTrue((duplicates == 1).all())
            print(f"  ✓ 去重功能正常: 无重复记录")


if __name__ == '__main__':
    unittest.main(verbosity=2)
# data_loader.py
"""数据加载模块 - 从CSV加载数据到ClickHouse"""

import pandas as pd
import os
from glob import glob
import logging
from clickhouse_driver import Client

logger = logging.getLogger(__name__)


class DataLoader:
    """数据加载器"""

    def __init__(self, client, data_dir):
        self.client = client
        self.data_dir = data_dir

    def load_all(self):
        """加载所有数据"""
        logger.info("开始加载CSV数据...")

        categories_df = self._load_categories()
        products_df = self._load_products()
        daily_df = self._load_daily_prices(products_df)

        self._upload_to_clickhouse(categories_df, products_df, daily_df)

        return categories_df, products_df, daily_df

    def _load_categories(self):
        """加载分类数据"""
        path = self._find_file('categories')
        logger.info(f"读取分类数据: {path}")

        df = self._read_file(path)
        df = df[df['category_id'].notna()]
        df['category_id'] = df['category_id'].astype(str).str.strip()
        logger.info(f"加载分类数据: {len(df)} 条")
        return df

    def _load_products(self):
        """加载商品数据"""
        path = self._find_file('products')
        logger.info(f"读取商品数据: {path}")

        df = self._read_file(path)
        df['product_id'] = df['product_id'].astype(str).str.strip()
        df['category_id'] = df['category_id'].astype(str).str.strip()
        logger.info(f"加载商品数据: {len(df)} 条")
        return df

    def _load_daily_prices(self, products_df):
        """加载日价格数据"""
        daily_dir = f'{self.data_dir}/daily_price'
        files = sorted(glob(f'{daily_dir}/daily_prices_*.csv')) + \
                sorted(glob(f'{daily_dir}/daily_prices_*.xlsx')) + \
                sorted(glob(f'{daily_dir}/daily_prices_*.xls'))

        logger.info(f"找到 {len(files)} 个日价格文件")

        dfs = []
        for file in files:
            try:
                df = self._read_file(file)
                df = self._standardize_daily_df(df, file, products_df)
                dfs.append(df)
                logger.info(f"  加载: {os.path.basename(file)}, {len(df)} 条")
            except Exception as e:
                logger.warning(f"  加载失败 {file}: {e}")

        if not dfs:
            raise ValueError("未找到任何日价格数据文件")

        daily_df = pd.concat(dfs, ignore_index=True)
        daily_df['date'] = pd.to_datetime(daily_df['date'])
        daily_df['category_id'] = daily_df['category_id'].astype(str).str.strip()

        logger.info(f"加载日价格数据: {len(daily_df)} 条")
        return daily_df

    def _find_file(self, name):
        """查找文件（支持多种扩展名）"""
        base = f'{self.data_dir}/{name}'
        for ext in ['.csv', '.xlsx', '.xls']:
            if os.path.exists(base + ext):
                return base + ext
        return base + '.csv'

    def _read_file(self, path):
        """读取文件（自动识别格式）"""
        if path.endswith('.csv'):
            return pd.read_csv(path)
        else:
            return pd.read_excel(path)

    def _standardize_daily_df(self, df, file, products_df):
        """标准化日价格DataFrame"""
        df.columns = df.columns.str.lower().str.strip()

        # 处理日期
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        elif 'change_date' in df.columns:
            df['date'] = pd.to_datetime(df['change_date'])
        else:
            date_str = os.path.basename(file).replace('daily_prices_', '').replace('.csv', '').replace('.xlsx',
                                                                                                       '').replace(
                '.xls', '')
            df['date'] = pd.to_datetime(date_str)

        # 标准化product_id
        if 'product_id' in df.columns:
            df['product_id'] = df['product_id'].astype(str).str.strip()

        # 关联category_id
        if 'category_id' not in df.columns and 'product_id' in df.columns:
            df = df.merge(products_df[['product_id', 'category_id']], on='product_id', how='left')

        return df

    def _upload_to_clickhouse(self, categories_df, products_df, daily_df):
        """上传数据到ClickHouse"""
        logger.info("上传数据到ClickHouse...")

        # 创建表
        self._create_tables()

        # 插入数据
        self._insert_categories(categories_df)
        self._insert_products(products_df)
        self._insert_daily_prices(daily_df)

        logger.info("数据上传完成!")

    def _create_tables(self):
        """创建ClickHouse表"""
        self.client.execute("DROP TABLE IF EXISTS categories")
        self.client.execute("DROP TABLE IF EXISTS products")
        self.client.execute("DROP TABLE IF EXISTS daily_prices")

        self.client.execute("""
            CREATE TABLE categories (
                category_id String,
                category_name String,
                hierarchy UInt8,
                weight Float64,
                price Nullable(Float64),
                parent String
            ) ENGINE = MergeTree()
            ORDER BY category_id
        """)

        self.client.execute("""
            CREATE TABLE products (
                product_id String,
                category_id String,
                name String,
                weight Float64,
                price Float64,
                change_count UInt32 DEFAULT 0
            ) ENGINE = MergeTree()
            ORDER BY product_id
        """)

        self.client.execute("""
            CREATE TABLE daily_prices (
                date Date,
                product_id String,
                category_id String,
                name String,
                price Float64,
                sales_qty UInt32 DEFAULT 0,
                sales_amount Float64 DEFAULT 0
            ) ENGINE = MergeTree()
            PARTITION BY date
            ORDER BY (date, product_id)
        """)

    def _insert_categories(self, df):
        df = df[['category_id', 'category_name', 'hierarchy', 'weight', 'parent']].copy()
        df['price'] = None
        self.client.insert_dataframe(
            'INSERT INTO categories (category_id, category_name, hierarchy, weight, price, parent) VALUES',
            df
        )
        logger.info(f"  上传分类: {len(df)} 条")

    def _insert_products(self, df):
        df = df[['product_id', 'category_id', 'name', 'weight', 'price']].copy()
        df['change_count'] = 0
        self.client.insert_dataframe(
            'INSERT INTO products (product_id, category_id, name, weight, price, change_count) VALUES',
            df
        )
        logger.info(f"  上传商品: {len(df)} 条")

    def _insert_daily_prices(self, df):
        df = df[['date', 'product_id', 'category_id', 'name', 'price']].copy()
        df['sales_qty'] = 0
        df['sales_amount'] = 0
        self.client.insert_dataframe(
            'INSERT INTO daily_prices (date, product_id, category_id, name, price, sales_qty, sales_amount) VALUES',
            df
        )
        logger.info(f"  上传日价格: {len(df)} 条")
# data_cleaner.py
"""数据清洗模块 - 异常值检测与处理"""

import pandas as pd
import logging
from clickhouse_driver import Client

logger = logging.getLogger(__name__)


class DataCleaner:
    """数据清洗器"""

    def __init__(self, client, params):
        self.client = client
        self.params = params

    def clean(self, start_date=None, end_date=None):
        """
        数据清洗 - 使用ClickHouse执行

        异常处理规则:
        1. 价格为空或<=0 -> 剔除
        2. 价格超过类目均值±3倍标准差 -> 剔除
        3. 价格超过类目均价5倍或低于0.2倍 -> 剔除
        4. 价格超过历史30天移动平均±20% -> 剔除
        5. 重复记录去重
        """
        logger.info("开始数据清洗...")

        # 获取日期范围
        if start_date is None:
            start_date = self._get_min_date()
        if end_date is None:
            end_date = self._get_max_date()

        # 统计原始数据量
        self._log_original_count(start_date, end_date)

        # 执行清洗SQL
        df = self._execute_clean_sql(start_date, end_date)

        self._log_clean_result(df)
        return df

    def _get_min_date(self):
        result = self.client.query_dataframe("SELECT MIN(date) as min_date FROM daily_prices")
        return result['min_date'].iloc[0] if not result.empty else '2025-05-17'

    def _get_max_date(self):
        result = self.client.query_dataframe("SELECT MAX(date) as max_date FROM daily_prices")
        return result['max_date'].iloc[0] if not result.empty else '2028-05-15'

    def _log_original_count(self, start_date, end_date):
        result = self.client.query_dataframe(f"""
            SELECT COUNT(*) as count FROM daily_prices 
            WHERE date BETWEEN '{start_date}' AND '{end_date}'
        """)
        logger.info(f"原始数据量: {result['count'].iloc[0] if not result.empty else 0} 条")

    def _execute_clean_sql(self, start_date, end_date):
        """执行清洗SQL"""
        p = self.params
        sql = f"""
        WITH 
        -- Step 1: 基础数据清理
        base_data AS (
            SELECT 
                d.date,
                d.product_id,
                p.category_id,
                p.name,
                d.price,
                p.weight,
                -- 计算历史N天移动平均
                AVG(d.price) OVER (
                    PARTITION BY d.product_id 
                    ORDER BY d.date 
                    ROWS BETWEEN {p['historical_window']} PRECEDING AND 1 PRECEDING
                ) as historical_avg
            FROM daily_prices d
            JOIN products p ON d.product_id = p.product_id
            WHERE d.date BETWEEN '{start_date}' AND '{end_date}'
                AND d.price IS NOT NULL
                AND d.price > 0
                AND p.category_id IS NOT NULL
        ),

        -- Step 2: 类目统计
        category_stats AS (
            SELECT 
                category_id,
                AVG(price) as category_avg,
                STDDEV(price) as category_std,
                COUNT(*) as cat_count
            FROM base_data
            GROUP BY category_id
            HAVING COUNT(*) >= {p['min_category_count']}
        ),

        -- Step 3: 异常标记
        cleaned AS (
            SELECT 
                b.*,
                cs.category_avg,
                cs.category_std,
                CASE 
                    -- 规则2: 超过类目均值±3倍标准差
                    WHEN cs.category_std IS NOT NULL 
                        AND ABS(b.price - cs.category_avg) > {p['std_threshold']} * cs.category_std 
                        THEN 1
                    -- 规则3: 超过类目均价5倍或低于0.2倍
                    WHEN cs.category_avg IS NOT NULL 
                        AND (b.price > cs.category_avg * {p['price_high_ratio']} 
                             OR b.price < cs.category_avg * {p['price_low_ratio']}) 
                        THEN 1
                    -- 规则4: 超过历史移动平均±20%
                    WHEN b.historical_avg IS NOT NULL 
                        AND (b.price > b.historical_avg * 1.2 
                             OR b.price < b.historical_avg * 0.8) 
                        THEN 1
                    ELSE 0
                END as is_anomaly
            FROM base_data b
            LEFT JOIN category_stats cs ON b.category_id = cs.category_id
        ),

        -- Step 4: 去重 (同日期同商品取平均价格)
        deduped AS (
            SELECT 
                date,
                product_id,
                category_id,
                name,
                AVG(price) as price,
                AVG(weight) as weight,
                category_avg,
                category_std
            FROM cleaned
            WHERE is_anomaly = 0
            GROUP BY date, product_id, category_id, name, category_avg, category_std
        )

        SELECT 
            date,
            product_id,
            category_id,
            name,
            price,
            weight
        FROM deduped
        ORDER BY date, product_id
        """

        return self.client.query_dataframe(sql)

    def _log_clean_result(self, df):
        logger.info(f"清洗完成: 保留 {len(df)} 条记录")
        if not df.empty:
            logger.info(f"  日期范围: {df['date'].min()} 至 {df['date'].max()}")
            logger.info(f"  商品数: {df['product_id'].nunique()}")
            logger.info(f"  分类数: {df['category_id'].nunique()}")
# data_cleaner.py
"""数据清洗模块 — 异常值检测与处理"""

import logging

logger = logging.getLogger(__name__)

from db import (
    TABLE_CATEGORIES, TABLE_DAILY_PRICES, TABLE_PRODUCTS, COL_DATE, COL_CATEGORY_NAME,
    get_date_range, get_daily_count, query_df
)


class DataCleaner:
    """数据清洗器"""

    def __init__(self, params):
        self.params = params

    def clean(self, start_date=None, end_date=None):
        """
        数据清洗 — 使用 ClickHouse 执行

        异常处理规则:
        1. 价格为空或<=0 → 剔除
        2. 价格超过类目均值±3倍标准差 → 剔除
        3. 价格超过类目均价5倍或低于0.2倍 → 剔除
        4. 价格超过历史30天移动平均±20% → 剔除
        5. 重复记录去重
        """
        logger.info("开始数据清洗...")

        # 获取日期范围
        if start_date is None:
            start_date, _ = get_date_range()
            if start_date is None:
                start_date = '2025-05-17'
        if end_date is None:
            _, end_date = get_date_range()
            if end_date is None:
                end_date = '2028-05-15'

        # 统计原始数据量
        self._log_original_count(start_date, end_date)

        # 执行清洗SQL
        df = self._execute_clean_sql(start_date, end_date)

        self._log_clean_result(df)
        return df

    def compute_category_daily(self, start_date=None, end_date=None):
        """
        在 ClickHouse 中完成清洗和二级分类日度聚合。
        """
        logger.info("开始计算清洗后的二级分类日度聚合...")

        if start_date is None:
            start_date, _ = get_date_range()
            if start_date is None:
                start_date = '2025-05-17'
        if end_date is None:
            _, end_date = get_date_range()
            if end_date is None:
                end_date = '2028-05-15'

        self._log_original_count(start_date, end_date)
        df = self._execute_category_daily_sql(start_date, end_date)

        logger.info(f"日度聚合完成: {len(df)} 条")
        if not df.empty:
            logger.info(f"  日期范围: {df['date'].min()} 至 {df['date'].max()}")
            logger.info(f"  二级分类数: {df['category_id'].nunique()}")
        return df

    def _log_original_count(self, start_date, end_date):
        count = get_daily_count(start_date, end_date)
        logger.info(f"原始数据量: {count} 条")

    def _execute_clean_sql(self, start_date, end_date):
        """执行清洗SQL（使用 toDate() 包裹日期字符串避免类型转换错误）"""
        p = self.params
        w = p.historical_window

        sql = f"""
        WITH base_data AS (
            SELECT
                d.{COL_DATE} AS date,
                d.product_id AS product_id,
                p.category_id AS category_id,
                p.name AS name,
                d.price AS price,
                p.weight AS weight,
                -- 类目均价和标准差（窗口函数）
                AVG(d.price) OVER (PARTITION BY p.category_id) AS category_avg,
                stddevPop(d.price) OVER (PARTITION BY p.category_id) AS category_std,
                COUNT(*) OVER (PARTITION BY p.category_id) AS cat_count,
                -- 历史N天移动平均
                AVG(d.price) OVER (
                    PARTITION BY d.product_id
                    ORDER BY d.{COL_DATE}
                    ROWS BETWEEN {w} PRECEDING AND 1 PRECEDING
                ) AS historical_avg
            FROM {TABLE_DAILY_PRICES} d
            JOIN {TABLE_PRODUCTS} p ON d.product_id = p.product_id
            WHERE d.{COL_DATE} BETWEEN toDate('{start_date}') AND toDate('{end_date}')
                AND d.price IS NOT NULL
                AND d.price > 0
                AND p.category_id IS NOT NULL
        ),
        cleaned AS (
            SELECT
                date, product_id, category_id, name, price, weight,
                category_avg, category_std, cat_count, historical_avg,
                CASE
                    WHEN cat_count >= {p.min_category_count}
                        AND category_std IS NOT NULL
                        AND category_std > 0
                        AND ABS(price - category_avg) > {p.std_threshold} * category_std
                        THEN 1
                    WHEN cat_count >= {p.min_category_count}
                        AND category_avg IS NOT NULL
                        AND category_avg > 0
                        AND (price > category_avg * {p.price_high_ratio}
                             OR price < category_avg * {p.price_low_ratio})
                        THEN 1
                    WHEN historical_avg IS NOT NULL
                        AND historical_avg > 0
                        AND (price > historical_avg * 1.2
                             OR price < historical_avg * 0.8)
                        THEN 1
                    ELSE 0
                END AS is_anomaly
            FROM base_data
            WHERE cat_count >= {p.min_category_count}
        ),
        deduped AS (
            SELECT
                date,
                product_id,
                category_id,
                name,
                AVG(price) AS price,
                AVG(weight) AS weight,
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

        df = query_df(sql, decode_strings=True)
        df['category_id'] = df['category_id'].astype(str).str.strip()
        return df

    def _execute_category_daily_sql(self, start_date, end_date):
        """执行低内存清洗聚合 SQL"""
        p = self.params
        w = p.historical_window

        sql = f"""
        WITH base_data AS (
            SELECT
                d.{COL_DATE} AS date,
                d.product_id AS product_id,
                p.category_id AS category_id,
                p.name AS name,
                d.price AS price,
                p.weight AS weight,
                AVG(d.price) OVER (PARTITION BY p.category_id) AS category_avg,
                stddevPop(d.price) OVER (PARTITION BY p.category_id) AS category_std,
                COUNT(*) OVER (PARTITION BY p.category_id) AS cat_count,
                AVG(d.price) OVER (
                    PARTITION BY d.product_id
                    ORDER BY d.{COL_DATE}
                    ROWS BETWEEN {w} PRECEDING AND 1 PRECEDING
                ) AS historical_avg
            FROM {TABLE_DAILY_PRICES} d
            JOIN {TABLE_PRODUCTS} p ON d.product_id = p.product_id
            WHERE d.{COL_DATE} BETWEEN toDate('{start_date}') AND toDate('{end_date}')
                AND d.price IS NOT NULL
                AND d.price > 0
                AND p.category_id IS NOT NULL
        ),
        cleaned AS (
            SELECT
                date, product_id, category_id, name, price, weight,
                category_avg, category_std, cat_count, historical_avg,
                CASE
                    WHEN cat_count >= {p.min_category_count}
                        AND category_std IS NOT NULL
                        AND category_std > 0
                        AND ABS(price - category_avg) > {p.std_threshold} * category_std
                        THEN 1
                    WHEN cat_count >= {p.min_category_count}
                        AND category_avg IS NOT NULL
                        AND category_avg > 0
                        AND (price > category_avg * {p.price_high_ratio}
                             OR price < category_avg * {p.price_low_ratio})
                        THEN 1
                    WHEN historical_avg IS NOT NULL
                        AND historical_avg > 0
                        AND (price > historical_avg * 1.2
                             OR price < historical_avg * 0.8)
                        THEN 1
                    ELSE 0
                END AS is_anomaly
            FROM base_data
            WHERE cat_count >= {p.min_category_count}
        ),
        deduped AS (
            SELECT
                date,
                product_id,
                category_id,
                name,
                AVG(price) AS price,
                AVG(weight) AS weight,
                category_avg,
                category_std
            FROM cleaned
            WHERE is_anomaly = 0
            GROUP BY date, product_id, category_id, name, category_avg, category_std
        ),
        category_mapped AS (
            SELECT
                d.date AS date,
                if(c.hierarchy = 3, c.parent, toString(d.category_id)) AS category_id,
                d.price AS clean_price,
                d.weight AS clean_weight
            FROM deduped d
            JOIN {TABLE_CATEGORIES} c ON d.category_id = c.category_id
        ),
        category_daily AS (
            SELECT
                date,
                toUInt64(category_id) AS category_id,
                SUM(clean_price * clean_weight * 1000) AS weighted_price,
                SUM(clean_weight * 1000) AS sales_qty,
                AVG(clean_price) AS price
            FROM category_mapped
            GROUP BY date, category_id
        )
        SELECT
            cd.date AS date,
            cd.category_id AS category_id,
            c.{COL_CATEGORY_NAME} AS category_name,
            cd.weighted_price AS weighted_price,
            cd.sales_qty AS sales_qty,
            cd.price AS price,
            cd.weighted_price / greatest(cd.sales_qty, 0.001) AS weighted_avg_price
        FROM category_daily cd
        JOIN {TABLE_CATEGORIES} c ON cd.category_id = c.category_id
        ORDER BY cd.date, cd.category_id
        """

        df = query_df(sql, decode_strings=True)
        df['category_id'] = df['category_id'].astype(str).str.strip()
        return df

    def _log_clean_result(self, df):
        logger.info(f"清洗完成: 保留 {len(df)} 条记录")
        if not df.empty:
            logger.info(f"  日期范围: {df['date'].min()} 至 {df['date'].max()}")
            logger.info(f"  商品数: {df['product_id'].nunique()}")
            logger.info(f"  分类数: {df['category_id'].nunique()}")

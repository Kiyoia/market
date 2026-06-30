# pipeline.py
"""价格指数计算流水线"""

import logging
import os
import time
from datetime import datetime

import pandas as pd

from config import settings
from data_cleaner import DataCleaner
from db import TABLE_PRICE_INDEX_RESULTS, execute, insert_df
from index_calculator import IndexCalculator
from visualizer import Visualizer

logger = logging.getLogger(__name__)


def ensure_result_table():
    """创建价格指数结果表"""
    execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_PRICE_INDEX_RESULTS} (
            date Date,
            category_id UInt64,
            category_name String,
            index_value Float64,
            weighted_price Float64,
            fisher Float64,
            global_index Nullable(Float64),
            created_at DateTime
        ) ENGINE = MergeTree()
        ORDER BY (date, category_id)
    """)


def prepare_result_df(index_df, aggregated_df):
    """合并分类指数和全局指数，生成固定结果契约"""
    if not aggregated_df.empty:
        result_df = index_df.merge(aggregated_df, on='date', how='left')
    else:
        result_df = index_df.copy()
        result_df['global_index'] = None

    result_df = result_df[[
        'date',
        'category_id',
        'category_name',
        'index_value',
        'weighted_price',
        'fisher',
        'global_index',
    ]].copy()
    result_df['date'] = pd.to_datetime(result_df['date']).dt.date
    result_df['category_id'] = result_df['category_id'].astype('uint64')
    result_df['category_name'] = result_df['category_name'].astype(str)
    result_df['created_at'] = datetime.now()
    return result_df.sort_values(['date', 'category_id'])


def write_results(result_df, output_path):
    """将结果写入 CSV 和 ClickHouse"""
    if result_df.empty:
        raise ValueError("无结果可写入")

    ensure_result_table()

    min_date = result_df['date'].min().strftime('%Y-%m-%d')
    max_date = result_df['date'].max().strftime('%Y-%m-%d')
    execute(f"""
        ALTER TABLE {TABLE_PRICE_INDEX_RESULTS}
        DELETE WHERE date BETWEEN toDate('{min_date}') AND toDate('{max_date}')
    """, settings={'mutations_sync': 1})

    insert_df(TABLE_PRICE_INDEX_RESULTS, result_df)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    result_df.to_csv(output_path, index=False)
    logger.info(f"结果已写入 ClickHouse 表: {TABLE_PRICE_INDEX_RESULTS}")
    logger.info(f"结果已保存: {output_path}")


def run_index_pipeline(save_chart=True):
    """运行完整价格指数计算流水线"""
    started_at = time.perf_counter()
    cleaner = DataCleaner(settings.anomaly_params)
    calculator = IndexCalculator(settings.base_date)

    category_daily = cleaner.compute_category_daily()
    if category_daily.empty:
        raise ValueError("无分类日度数据")

    index_df = calculator.compute_chain_price_index(category_daily)
    if index_df.empty:
        raise ValueError("指数计算失败")

    aggregated_df = calculator.compute_aggregated_index(index_df, level='global')
    result_df = prepare_result_df(index_df, aggregated_df)
    logger.info(f"结果生成完成: {len(result_df)} 条")

    output_path = os.path.join(settings.data_dir, 'price_index_results.csv')
    write_results(result_df, output_path)

    if save_chart:
        Visualizer().plot_price_index(
            result_df,
            title=f"高频电商价格指数趋势图 (基期: {settings.base_date})",
            save_path=os.path.join(settings.data_dir, 'price_index_trend.png'),
            show=False
        )

    logger.info(f"流水线总耗时: {time.perf_counter() - started_at:.2f} 秒")
    return result_df

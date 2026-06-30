# db.py
"""数据库操作模块 — 统一管理 ClickHouse 连接、表名常量和查询操作"""

import logging
from typing import Any

import pandas as pd
from clickhouse_driver import Client

from config import CLICKHOUSE_CONFIG

logger = logging.getLogger(__name__)

# ==================== 连接管理（单例） ====================

_client = None


def get_client():
    """获取 ClickHouse 客户端单例"""
    global _client
    if _client is None:
        _client = Client(
            host=CLICKHOUSE_CONFIG['host'],
            port=CLICKHOUSE_CONFIG['port'],
            database=CLICKHOUSE_CONFIG['database'],
            user=CLICKHOUSE_CONFIG['user'],
            password=CLICKHOUSE_CONFIG['password'],
            settings={
                'use_numpy': True,
                'strings_encoding': 'gb18030',
            }
        )
    return _client


# ==================== 表名常量 ====================

TABLE_CATEGORIES = 'oss_categories'
TABLE_PRODUCTS = 'oss_products'
TABLE_DAILY_PRICES = 'oss_daily_prices'
TABLE_PRICE_INDEX_RESULTS = 'price_index_results'

# 当前线上源表字段
COL_DATE = 'change_date'
COL_CATEGORY_NAME = 'category'


# ==================== 基础查询工具 ====================

def decode_gb18030(value: object) -> object:
    """将 ClickHouse 中的 GB18030 字节直接解码为文本"""
    if isinstance(value, bytes):
        return value.decode('gb18030')
    return value


def decode_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """解码 DataFrame 中的字节列"""
    if df.empty:
        return df

    decoded = df.copy()
    for column in decoded.columns:
        if decoded[column].dtype == object:
            decoded[column] = decoded[column].map(decode_gb18030)
    return decoded


def query_df(sql: str, params: dict[str, object] | None = None, decode_strings: bool = False) -> pd.DataFrame:
    """执行查询并返回 DataFrame"""
    settings = {'strings_as_bytes': True} if decode_strings else None
    df = get_client().query_dataframe(sql, params=params, settings=settings)
    if decode_strings:
        df = decode_text_columns(df)
    return df


def execute(sql: str, params: dict[str, object] | None = None, settings: dict[str, object] | None = None) -> Any:
    """执行非查询语句（INSERT/DROP/CREATE 等）"""
    return get_client().execute(sql, params=params, settings=settings)


def insert_df(table: str, df: pd.DataFrame) -> object:
    """将 DataFrame 插入表"""
    columns = ', '.join(df.columns)
    return get_client().insert_dataframe(
        f'INSERT INTO {table} ({columns}) VALUES',
        df
    )


# ==================== 业务查询封装 ====================

def get_date_range():
    """获取每日价格表的日期范围，返回 (min_date, max_date)"""
    result = query_df(f"""
        SELECT MIN({COL_DATE}) AS min_date, MAX({COL_DATE}) AS max_date
        FROM {TABLE_DAILY_PRICES}
    """)
    min_date = result['min_date'].iloc[0] if not result.empty else None
    max_date = result['max_date'].iloc[0] if not result.empty else None
    return min_date, max_date


def get_daily_count(start_date=None, end_date=None):
    """获取每日价格表的记录数"""
    conditions = []
    if start_date:
        conditions.append(f"{COL_DATE} >= toDate('{start_date}')")
    if end_date:
        conditions.append(f"{COL_DATE} <= toDate('{end_date}')")
    where = ' AND '.join(conditions) if conditions else '1=1'
    result = query_df(f"""
        SELECT COUNT(*) AS count FROM {TABLE_DAILY_PRICES}
        WHERE {where}
    """)
    return int(result['count'].iloc[0]) if not result.empty else 0


def get_categories(hierarchy=None):
    """获取分类列表，返回语义列名 category_name"""
    sql = f"""
        SELECT
            category_id,
            {COL_CATEGORY_NAME} AS category_name,
            hierarchy,
            weight,
            parent
        FROM {TABLE_CATEGORIES}
    """
    if hierarchy is not None:
        sql += f' WHERE hierarchy = {int(hierarchy)}'
    sql += ' ORDER BY hierarchy, category_id'

    df = query_df(sql, decode_strings=True)
    df['category_id'] = df['category_id'].astype(str).str.strip()
    df['parent'] = df['parent'].astype(str).str.strip()
    return df


def get_top_categories(limit=5):
    """获取权重最大的 N 个二级分类"""
    result = query_df(f"""
        SELECT {COL_CATEGORY_NAME} AS category_name, weight
        FROM {TABLE_CATEGORIES}
        WHERE hierarchy = 2
        ORDER BY weight DESC
        LIMIT {int(limit)}
    """, decode_strings=True)
    return result['category_name'].tolist()


def get_table_stats():
    """获取各表统计信息"""
    stats = {}
    for name, table in [
        ('daily_prices', TABLE_DAILY_PRICES),
        ('products', TABLE_PRODUCTS),
        ('categories', TABLE_CATEGORIES),
    ]:
        result = query_df(f'SELECT COUNT(*) AS count FROM {table}')
        stats[name] = int(result['count'].iloc[0]) if not result.empty else 0
    return stats


def result_table_exists() -> bool:
    """判断结果表是否存在"""
    result = query_df(f'SELECT 1 FROM system.tables WHERE name = %(table)s LIMIT 1', {
        'table': TABLE_PRICE_INDEX_RESULTS
    })
    return not result.empty


def query_price_index_results(start_date=None, end_date=None, category_id=None, limit=100):
    """查询价格指数结果表"""
    conditions = []
    params: dict[str, object] = {'limit': int(limit)}

    if start_date:
        conditions.append('date >= toDate(%(start_date)s)')
        params['start_date'] = start_date
    if end_date:
        conditions.append('date <= toDate(%(end_date)s)')
        params['end_date'] = end_date
    if category_id:
        conditions.append('category_id = toUInt64(%(category_id)s)')
        params['category_id'] = str(category_id)

    where = ' AND '.join(conditions) if conditions else '1=1'
    return query_df(f"""
        SELECT
            date,
            category_id,
            category_name,
            index_value,
            weighted_price,
            fisher,
            global_index,
            created_at
        FROM {TABLE_PRICE_INDEX_RESULTS}
        WHERE {where}
        ORDER BY date DESC, category_id
        LIMIT %(limit)s
    """, params=params, decode_strings=True)


def get_latest_price_index_summary():
    """读取最新全局指数摘要"""
    return query_df(f"""
        SELECT
            max(date) AS latest_date,
            anyLast(global_index) AS global_index,
            count() AS total_records,
            uniqExact(category_id) AS categories
        FROM {TABLE_PRICE_INDEX_RESULTS}
        WHERE date = (SELECT max(date) FROM {TABLE_PRICE_INDEX_RESULTS})
    """)

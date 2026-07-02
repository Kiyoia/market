"""测试数据构造函数"""

from pathlib import Path
from types import SimpleNamespace

import pandas as pd


def standard_categories_df() -> pd.DataFrame:
    """构造三个二级分类"""
    return pd.DataFrame({
        "category_id": ["1101010000", "1101020000", "1101030000"],
        "category_name": ["粮食", "淀粉", "食用油"],
        "hierarchy": [2, 2, 2],
        "weight": [0.5, 0.3, 0.2],
        "parent": ["1101000000", "1101000000", "1101000000"],
    })


def standard_products_df() -> pd.DataFrame:
    """构造五个属于不同类目的商品"""
    return pd.DataFrame({
        "product_id": ["1001", "1002", "1003", "1004", "1005"],
        "category_id": [
            "1101010000",
            "1101010000",
            "1101020000",
            "1101030000",
            "1101030000",
        ],
        "name": ["大米", "面粉", "玉米淀粉", "花生油", "菜籽油"],
        "weight": [0.10, 0.20, 0.15, 0.25, 0.30],
        "price": [10.0, 12.0, 8.0, 30.0, 28.0],
    })


def standard_daily_prices_df() -> pd.DataFrame:
    """构造连续三天的日价格记录"""
    rows = []
    prices_by_date = {
        "2025-05-17": [10.0, 12.0, 8.0, 30.0, 28.0],
        "2025-05-18": [11.0, 12.6, 8.4, 31.5, 27.0],
        "2025-05-19": [10.5, 13.2, 8.2, 33.0, 29.0],
    }
    products = standard_products_df()

    for date, prices in prices_by_date.items():
        for product, price in zip(products.to_dict("records"), prices):
            rows.append({
                "date": pd.to_datetime(date),
                "product_id": product["product_id"],
                "category_id": product["category_id"],
                "name": product["name"],
                "price": price,
                "sales_qty": 100,
            })
    return pd.DataFrame(rows)


def create_standard_data_files(data_dir: str | Path) -> None:
    """创建图片说明中的分类、商品和三天日价格测试文件"""
    data_path = Path(data_dir)
    daily_path = data_path / "daily_price"
    daily_path.mkdir(parents=True, exist_ok=True)

    standard_categories_df().to_csv(data_path / "categories.csv", index=False)
    standard_products_df().to_csv(data_path / "products.csv", index=False)

    daily_df = standard_daily_prices_df()
    for date, group in daily_df.groupby(daily_df["date"].dt.strftime("%Y%m%d")):
        file_df = group[["product_id", "name", "price", "sales_qty"]].copy()
        file_df.to_csv(daily_path / f"daily_prices_{date}.csv", index=False)


def dirty_price_records_df() -> pd.DataFrame:
    """构造包含高价、低价和空价格的清洗测试数据"""
    return pd.DataFrame({
        "date": pd.to_datetime(["2025-05-17"] * 5),
        "product_id": ["1001", "1002", "1003", "1004", "1005"],
        "category_id": [
            "1101010000",
            "1101010000",
            "1101020000",
            "1101030000",
            "1101030000",
        ],
        "name": ["大米", "面粉", "玉米淀粉", "花生油", "菜籽油"],
        "price": [10.0, 12.0, None, 999.99, 3.33],
        "weight": [0.10, 0.20, 0.15, 0.25, 0.30],
    })


def cleaned_price_records_df() -> pd.DataFrame:
    """构造清洗后的正常价格数据"""
    return pd.DataFrame({
        "date": pd.to_datetime(["2025-05-17", "2025-05-17"]),
        "product_id": ["1001", "1002"],
        "category_id": ["1101010000", "1101010000"],
        "name": ["大米", "面粉"],
        "price": [10.0, 12.0],
        "weight": [0.10, 0.20],
    })


def category_daily_df() -> pd.DataFrame:
    """构造分类日度聚合结果"""
    return pd.DataFrame({
        "date": pd.to_datetime(["2025-05-17"]),
        "category_id": ["1101010000"],
        "category_name": ["粮食"],
        "weighted_price": [3400.0],
        "sales_qty": [300.0],
        "price": [11.0],
        "weighted_avg_price": [3400.0 / 300.0],
    })


def category_daily_for_index_df() -> pd.DataFrame:
    """构造指数计算所需的连续日期分类日度数据"""
    return pd.DataFrame({
        "date": pd.to_datetime([
            "2025-05-17",
            "2025-05-18",
            "2025-05-19",
            "2025-05-17",
            "2025-05-18",
            "2025-05-19",
        ]),
        "category_id": [
            "1101010000",
            "1101010000",
            "1101010000",
            "1101020000",
            "1101020000",
            "1101020000",
        ],
        "category_name": ["粮食", "粮食", "粮食", "淀粉", "淀粉", "淀粉"],
        "weighted_avg_price": [10.0, 11.0, 10.5, 8.0, 8.4, 8.2],
        "sales_qty": [100, 100, 100, 80, 80, 80],
    })


def index_categories_df() -> pd.DataFrame:
    """构造指数汇总所需的二级分类权重"""
    return pd.DataFrame({
        "category_id": ["1101010000", "1101020000"],
        "category_name": ["粮食", "淀粉"],
        "hierarchy": [2, 2],
        "weight": [0.75, 0.25],
        "parent": ["1101000000", "1101000000"],
    })


def loose_anomaly_params():
    """构造云端测试用的宽松清洗参数"""
    return SimpleNamespace(
        std_threshold=1000,
        price_high_ratio=1000,
        price_low_ratio=0.001,
        historical_window=30,
        min_category_count=1,
    )

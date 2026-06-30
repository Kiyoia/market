# api.py
"""API接口模块 — 提供RESTful API"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import pandas as pd
import matplotlib.pyplot as plt
import logging
import base64
from io import BytesIO

from config import DATA_DIR
from db import (
    get_client,
    get_categories,
    get_date_range,
    get_latest_price_index_summary,
    get_table_stats,
    query_price_index_results,
    result_table_exists,
)
from data_cleaner import DataCleaner
from data_loader import DataLoader
from pipeline import run_index_pipeline
from config import ANOMALY_PARAMS

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(
    title="高频电商价格指数计算平台 API",
    description="提供价格指数计算、查询、可视化等接口",
    version="1.0.0"
)

# 全局缓存
index_cache = {}
cache_time = None


# ==================== 数据模型 ====================

class LoadDataRequest(BaseModel):
    """加载数据请求"""
    data_dir: Optional[str] = Field(None, description="数据目录路径")
    force_reload: bool = Field(False, description="是否强制重新加载")


class CleanDataRequest(BaseModel):
    """清洗数据请求"""
    start_date: Optional[str] = Field(None, description="开始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="结束日期 YYYY-MM-DD")


# ==================== 工具函数 ====================

def df_to_records(df):
    """将 DataFrame 转成 JSON 友好的记录"""
    if df.empty:
        return []

    result = df.copy()
    for column in result.columns:
        if column == 'created_at':
            result[column] = pd.to_datetime(result[column]).dt.strftime('%Y-%m-%d %H:%M:%S')
        elif pd.api.types.is_datetime64_any_dtype(result[column]):
            result[column] = result[column].dt.strftime('%Y-%m-%d')
        elif column == 'date':
            result[column] = pd.to_datetime(result[column]).dt.strftime('%Y-%m-%d')
    return result.to_dict('records')


# ==================== API接口 ====================

@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "高频电商价格指数计算平台",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    try:
        client = get_client()
        result = client.query_dataframe("SELECT 1 as test")
        return {
            "status": "healthy",
            "clickhouse": "connected",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.post("/api/data/load")
async def load_data(request: LoadDataRequest):
    """
    加载数据到ClickHouse

    - 从CSV文件加载数据
    - 自动识别文件格式（csv/xlsx/xls）
    - 上传到ClickHouse数据库
    """
    logger.info(f"收到加载数据请求: {request}")

    try:
        data_dir = request.data_dir or DATA_DIR
        loader = DataLoader(data_dir, force_reload=request.force_reload)

        categories_df, products_df, daily_df = loader.load_all()

        return {
            "code": 0,
            "message": "数据加载成功",
            "data": {
                "categories_count": len(categories_df),
                "products_count": len(products_df),
                "daily_prices_count": len(daily_df),
                "date_range": {
                    "start": daily_df['date'].min().strftime('%Y-%m-%d'),
                    "end": daily_df['date'].max().strftime('%Y-%m-%d')
                }
            }
        }
    except Exception as e:
        logger.error(f"加载数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/data/clean")
async def clean_data(request: CleanDataRequest):
    """
    清洗数据

    - 剔除空值/无效价格
    - 异常值检测（3倍标准差）
    - 去重处理
    """
    logger.info(f"收到清洗数据请求: {request}")

    try:
        cleaner = DataCleaner(ANOMALY_PARAMS)
        category_daily = cleaner.compute_category_daily(
            start_date=request.start_date,
            end_date=request.end_date
        )

        return {
            "code": 0,
            "message": "数据清洗聚合完成",
            "data": {
                "total_records": len(category_daily),
                "date_range": {
                    "start": category_daily['date'].min().strftime('%Y-%m-%d') if not category_daily.empty else None,
                    "end": category_daily['date'].max().strftime('%Y-%m-%d') if not category_daily.empty else None
                } if not category_daily.empty else None,
                "sample": df_to_records(category_daily.head(10)) if not category_daily.empty else []
            }
        }
    except Exception as e:
        logger.error(f"清洗数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/index/calculate")
async def calculate_index(background_tasks: BackgroundTasks):
    """
    计算价格指数（异步任务）

    - 执行完整的指数计算流程
    - 结果保存在数据库和CSV中
    """
    logger.info("收到计算指数请求")

    def run_calculation():
        try:
            result_df = run_index_pipeline(save_chart=True)
            global index_cache, cache_time
            index_cache = {
                'result': result_df,
                'summary': {
                    'total_records': len(result_df),
                    'categories': result_df['category_id'].nunique(),
                    'date_range': {
                        'start': result_df['date'].min().strftime('%Y-%m-%d'),
                        'end': result_df['date'].max().strftime('%Y-%m-%d')
                    },
                    'latest_index': float(result_df['global_index'].dropna().iloc[-1])
                }
            }
            cache_time = datetime.now()

            logger.info("指数计算完成")

        except Exception as e:
            logger.error(f"计算指数失败: {e}")

    # 异步执行
    background_tasks.add_task(run_calculation)

    return {
        "code": 0,
        "message": "计算任务已提交，请稍后查询结果",
        "status": "processing"
    }


@app.get("/api/index/query")
async def query_index(
        start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
        end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
        category_id: Optional[str] = Query(None, description="分类ID"),
        level: str = Query("global", description="聚合级别: global/category/sku"),
        limit: int = Query(100, description="返回条数", ge=1, le=1000)
):
    """
    查询价格指数

    - 按日期范围查询
    - 按分类筛选
    - 支持不同聚合级别
    """
    logger.info(f"收到查询指数请求: start={start_date}, end={end_date}, category={category_id}")

    try:
        if not result_table_exists():
            raise HTTPException(status_code=404, detail="未找到指数结果表，请先计算")

        df = query_price_index_results(
            start_date=start_date,
            end_date=end_date,
            category_id=category_id,
            limit=limit
        )

        if df.empty:
            return {
                "code": 0,
                "data": [],
                "total": 0
            }

        return {
            "code": 0,
            "data": df_to_records(df),
            "total": len(df)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询指数失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/index/latest")
async def get_latest_index():
    """获取最新指数"""
    logger.info("获取最新指数")

    try:
        # 优先从缓存读取
        global index_cache, cache_time
        if index_cache and cache_time:
            # 缓存5分钟内有效
            if (datetime.now() - cache_time).seconds < 300:
                return {
                    "code": 0,
                    "data": index_cache['summary'],
                    "cached": True
                }

        if not result_table_exists():
            return {
                "code": 1,
                "message": "暂无指数数据，请先计算",
                "data": None
            }

        summary = get_latest_price_index_summary()
        if summary.empty or pd.isna(summary['latest_date'].iloc[0]):
            return {
                "code": 1,
                "message": "暂无指数数据，请先计算",
                "data": None
            }

        return {
            "code": 0,
            "data": {
                "date": summary['latest_date'].iloc[0].strftime('%Y-%m-%d'),
                "global_index": float(summary['global_index'].iloc[0]),
                "total_records": int(summary['total_records'].iloc[0]),
                "categories": int(summary['categories'].iloc[0])
            },
            "cached": False
        }

    except Exception as e:
        logger.error(f"获取最新指数失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/index/chart")
async def get_index_chart(
        width: int = Query(1200, description="图表宽度"),
        height: int = Query(800, description="图表高度"),
        start_date: Optional[str] = Query(None, description="开始日期"),
        end_date: Optional[str] = Query(None, description="结束日期")
):
    """
    获取指数趋势图
    """
    logger.info("获取指数趋势图")

    try:
        if not result_table_exists():
            raise HTTPException(status_code=404, detail="未找到指数结果表，请先计算")

        df = query_price_index_results(
            start_date=start_date,
            end_date=end_date,
            limit=100000
        )
        df['date'] = pd.to_datetime(df['date'])

        if df.empty:
            raise HTTPException(status_code=404, detail="指定日期范围内无数据")

        # 设置图表大小
        plt.rcParams['figure.figsize'] = (width / 100, height / 100)

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(width / 100, height / 100))

        # 全局指数
        if 'global_index' in df.columns:
            ax1.plot(df['date'], df['global_index'], linewidth=2, color='darkblue')
            ax1.set_title('全局价格指数趋势', fontsize=14)
            ax1.set_ylabel('指数 (基期=100)')
            ax1.grid(True, alpha=0.3)
            ax1.axhline(y=100, color='red', linestyle='--', alpha=0.5)

        # 分类指数（Top 5）
        from db import get_top_categories
        top_categories = get_top_categories(5)

        pivot_df = df.pivot_table(index='date', columns='category_name', values='index_value')
        for cat in top_categories:
            if cat in pivot_df.columns:
                pivot_df[cat].plot(ax=ax2, linewidth=1.5, label=cat)

        ax2.set_title('主要分类价格指数趋势', fontsize=14)
        ax2.set_xlabel('日期')
        ax2.set_ylabel('指数 (基期=100)')
        ax2.legend(loc='upper left', fontsize=8)
        ax2.grid(True, alpha=0.3)
        ax2.axhline(y=100, color='red', linestyle='--', alpha=0.5)

        plt.tight_layout()

        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight')
        plt.close()
        buffer.seek(0)
        image_data = base64.b64encode(buffer.read()).decode('utf-8')

        return {
            "code": 0,
            "data": {
                "image": f"data:image/png;base64,{image_data}",
                "date_range": {
                    "start": df['date'].min().strftime('%Y-%m-%d'),
                    "end": df['date'].max().strftime('%Y-%m-%d')
                }
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取图表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/categories")
async def api_get_categories():
    """
    获取分类列表
    """
    logger.info("获取分类列表")

    try:
        df = get_categories()

        return {
            "code": 0,
            "data": df_to_records(df),
            "total": len(df)
        }
    except Exception as e:
        logger.error(f"获取分类失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
async def get_stats():
    """
    获取系统统计信息
    """
    logger.info("获取系统统计信息")

    try:
        stats = get_table_stats()
        min_date, max_date = get_date_range()

        return {
            "code": 0,
            "data": {
                "daily_prices": stats['daily_prices'],
                "products": stats['products'],
                "categories": stats['categories'],
                "date_range": {
                    "start": min_date.strftime('%Y-%m-%d') if min_date else None,
                    "end": max_date.strftime('%Y-%m-%d') if max_date else None,
                },
                "has_index": result_table_exists()
            }
        }
    except Exception as e:
        logger.error(f"获取统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/index/refresh")
async def refresh_index():
    """
    刷新指数数据（重新计算）
    """
    logger.info("刷新指数数据")

    try:
        result_df = run_index_pipeline(save_chart=True)

        return {
            "code": 0,
            "message": "指数刷新成功",
            "data": {
                "total_records": len(result_df),
                "date_range": {
                    "start": result_df['date'].min().strftime('%Y-%m-%d'),
                    "end": result_df['date'].max().strftime('%Y-%m-%d')
                }
            }
        }
    except Exception as e:
        logger.error(f"刷新指数失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

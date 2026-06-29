# api.py
"""API接口模块 - 提供RESTful API"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date
import pandas as pd
import logging
import os
from glob import glob

from config import CLICKHOUSE_CONFIG, DATA_DIR, BASE_DATE, ANOMALY_PARAMS
from data_loader import DataLoader
from data_cleaner import DataCleaner
from index_calculator import IndexCalculator
from visualizer import Visualizer

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


class IndexQueryRequest(BaseModel):
    """指数查询请求"""
    start_date: Optional[str] = Field(None, description="开始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="结束日期 YYYY-MM-DD")
    category_ids: Optional[List[str]] = Field(None, description="分类ID列表")
    level: str = Field("global", description="聚合级别: global/category/sku")


class IndexResponse(BaseModel):
    """指数响应"""
    date: str
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    index_value: float
    global_index: Optional[float] = None


# ==================== 工具函数 ====================

def get_client():
    """获取ClickHouse连接"""
    from clickhouse_driver import Client
    return Client(
        host=CLICKHOUSE_CONFIG['host'],
        port=CLICKHOUSE_CONFIG['port'],
        database=CLICKHOUSE_CONFIG['database'],
        user=CLICKHOUSE_CONFIG['user'],
        password=CLICKHOUSE_CONFIG['password'],
        settings={'use_numpy': True}
    )


def get_calculator():
    """获取计算器实例"""
    client = get_client()
    return {
        'loader': DataLoader(client, DATA_DIR),
        'cleaner': DataCleaner(client, ANOMALY_PARAMS),
        'calculator': IndexCalculator(client, BASE_DATE),
        'visualizer': Visualizer(client),
        'client': client
    }


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
        tools = get_calculator()

        # 使用自定义数据目录
        if request.data_dir:
            tools['loader'].data_dir = request.data_dir

        categories_df, products_df, daily_df = tools['loader'].load_all()

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
        tools = get_calculator()
        cleaned_df = tools['cleaner'].clean(
            start_date=request.start_date,
            end_date=request.end_date
        )

        return {
            "code": 0,
            "message": "数据清洗完成",
            "data": {
                "total_records": len(cleaned_df),
                "date_range": {
                    "start": cleaned_df['date'].min().strftime('%Y-%m-%d') if not cleaned_df.empty else None,
                    "end": cleaned_df['date'].max().strftime('%Y-%m-%d') if not cleaned_df.empty else None
                } if not cleaned_df.empty else None,
                "sample": cleaned_df.head(10).to_dict('records') if not cleaned_df.empty else []
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
            tools = get_calculator()

            # 1. 清洗数据
            cleaned_df = tools['cleaner'].clean()

            if cleaned_df.empty:
                logger.warning("清洗后无数据")
                return

            # 2. 计算加权平均价格
            category_daily = tools['calculator'].compute_weighted_avg_price(cleaned_df)

            if category_daily.empty:
                logger.warning("无分类日度数据")
                return

            # 3. 计算链式指数
            index_df = tools['calculator'].compute_chain_price_index(category_daily)

            if index_df.empty:
                logger.warning("指数计算失败")
                return

            # 4. 汇总指数
            aggregated_df = tools['calculator'].compute_aggregated_index(index_df, level='global')

            # 5. 合并结果
            if not aggregated_df.empty:
                result_df = index_df.merge(aggregated_df, on='date', how='left')
            else:
                result_df = index_df

            # 6. 保存结果
            output_path = f'{DATA_DIR}/price_index_results.csv'
            result_df.to_csv(output_path, index=False)
            logger.info(f"结果已保存: {output_path}")

            # 7. 生成图表
            tools['visualizer'].plot_price_index(
                result_df,
                title=f"高频电商价格指数趋势图 (基期: {BASE_DATE})",
                save_path=f'{DATA_DIR}/price_index_trend.png'
            )

            # 8. 缓存结果
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
                    'latest_index': float(
                        result_df['global_index'].iloc[-1]) if 'global_index' in result_df.columns else None
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
        client = get_client()

        # 构建SQL
        sql = """
        SELECT 
            date,
            category_id,
            category_name,
            index_value
        FROM price_index_results
        WHERE 1=1
        """

        params = []
        if start_date:
            sql += " AND date >= %(start_date)s"
            params.append(('start_date', start_date))
        if end_date:
            sql += " AND date <= %(end_date)s"
            params.append(('end_date', end_date))
        if category_id:
            sql += " AND category_id = %(category_id)s"
            params.append(('category_id', category_id))

        sql += " ORDER BY date DESC LIMIT %(limit)s"
        params.append(('limit', limit))

        # 执行查询
        df = client.query_dataframe(sql)

        if df.empty:
            return {
                "code": 0,
                "data": [],
                "total": 0
            }

        return {
            "code": 0,
            "data": df.to_dict('records'),
            "total": len(df)
        }

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

        # 从CSV读取
        result_path = f'{DATA_DIR}/price_index_results.csv'
        if os.path.exists(result_path):
            df = pd.read_csv(result_path)
            df['date'] = pd.to_datetime(df['date'])

            latest = df.iloc[-1]
            return {
                "code": 0,
                "data": {
                    "date": latest['date'].strftime('%Y-%m-%d'),
                    "global_index": float(latest['global_index']) if 'global_index' in latest else None,
                    "total_records": len(df),
                    "categories": df['category_id'].nunique()
                },
                "cached": False
            }

        return {
            "code": 1,
            "message": "暂无指数数据，请先计算",
            "data": None
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
        # 读取数据
        result_path = f'{DATA_DIR}/price_index_results.csv'
        if not os.path.exists(result_path):
            raise HTTPException(status_code=404, detail="未找到指数数据，请先计算")

        df = pd.read_csv(result_path)
        df['date'] = pd.to_datetime(df['date'])

        # 筛选日期
        if start_date:
            df = df[df['date'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['date'] <= pd.to_datetime(end_date)]

        if df.empty:
            raise HTTPException(status_code=404, detail="指定日期范围内无数据")

        # 生成图表
        tools = get_calculator()

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
        categories = tools['client'].query_dataframe(
            "SELECT category_name FROM categories WHERE hierarchy = 2 ORDER BY weight DESC LIMIT 5"
        )
        top_categories = categories['category_name'].tolist()

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

        # 保存临时图片
        temp_path = f'{DATA_DIR}/temp_chart.png'
        plt.savefig(temp_path, dpi=150, bbox_inches='tight')
        plt.close()

        # 读取图片返回
        import base64
        with open(temp_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')

        os.remove(temp_path)

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
async def get_categories():
    """
    获取分类列表
    """
    logger.info("获取分类列表")

    try:
        client = get_client()
        df = client.query_dataframe(
            "SELECT category_id, category_name, hierarchy, weight, parent FROM categories ORDER BY hierarchy, category_id"
        )

        return {
            "code": 0,
            "data": df.to_dict('records'),
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
        client = get_client()

        # 数据统计
        daily_count = client.query_dataframe("SELECT COUNT(*) as count FROM daily_prices")
        product_count = client.query_dataframe("SELECT COUNT(*) as count FROM products")
        category_count = client.query_dataframe("SELECT COUNT(*) as count FROM categories")

        # 日期范围
        date_range = client.query_dataframe("""
            SELECT MIN(date) as min_date, MAX(date) as max_date, COUNT(DISTINCT date) as days
            FROM daily_prices
        """)

        return {
            "code": 0,
            "data": {
                "daily_prices": int(daily_count['count'].iloc[0]) if not daily_count.empty else 0,
                "products": int(product_count['count'].iloc[0]) if not product_count.empty else 0,
                "categories": int(category_count['count'].iloc[0]) if not category_count.empty else 0,
                "date_range": {
                    "start": date_range['min_date'].iloc[0].strftime('%Y-%m-%d') if not date_range.empty and
                                                                                    date_range['min_date'].iloc[
                                                                                        0] else None,
                    "end": date_range['max_date'].iloc[0].strftime('%Y-%m-%d') if not date_range.empty and
                                                                                  date_range['max_date'].iloc[
                                                                                      0] else None,
                    "days": int(date_range['days'].iloc[0]) if not date_range.empty else 0
                },
                "has_index": os.path.exists(f'{DATA_DIR}/price_index_results.csv')
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
        tools = get_calculator()

        # 重新计算
        cleaned_df = tools['cleaner'].clean()
        category_daily = tools['calculator'].compute_weighted_avg_price(cleaned_df)
        index_df = tools['calculator'].compute_chain_price_index(category_daily)
        aggregated_df = tools['calculator'].compute_aggregated_index(index_df, level='global')

        if not aggregated_df.empty:
            result_df = index_df.merge(aggregated_df, on='date', how='left')
        else:
            result_df = index_df

        # 保存
        output_path = f'{DATA_DIR}/price_index_results.csv'
        result_df.to_csv(output_path, index=False)

        # 生成图表
        tools['visualizer'].plot_price_index(
            result_df,
            title=f"高频电商价格指数趋势图 (基期: {BASE_DATE})",
            save_path=f'{DATA_DIR}/price_index_trend.png'
        )

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
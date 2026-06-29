# main.py
"""主程序入口"""

import logging
from clickhouse_driver import Client

from config import CLICKHOUSE_CONFIG, DATA_DIR, BASE_DATE, ANOMALY_PARAMS, LOG_CONFIG
from data_loader import DataLoader
from data_cleaner import DataCleaner
from index_calculator import IndexCalculator
from visualizer import Visualizer

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_CONFIG['level']),
    format=LOG_CONFIG['format']
)
logger = logging.getLogger(__name__)


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("高频电商价格指数计算平台")
    logger.info("=" * 60)

    # 1. 连接ClickHouse
    client = Client(
        host=CLICKHOUSE_CONFIG['host'],
        port=CLICKHOUSE_CONFIG['port'],
        database=CLICKHOUSE_CONFIG['database'],
        user=CLICKHOUSE_CONFIG['user'],
        password=CLICKHOUSE_CONFIG['password'],
        settings={'use_numpy': True}
    )

    try:
        # 2. 加载数据
        loader = DataLoader(client, DATA_DIR)
        categories_df, products_df, daily_df = loader.load_all()

        # 3. 数据清洗
        cleaner = DataCleaner(client, ANOMALY_PARAMS)
        cleaned_df = cleaner.clean()

        if cleaned_df.empty:
            logger.warning("清洗后无数据，请检查数据源")
            return

        # 4. 指数计算
        calculator = IndexCalculator(client, BASE_DATE)
        category_daily = calculator.compute_weighted_avg_price(cleaned_df)

        if category_daily.empty:
            logger.warning("无分类日度数据")
            return

        index_df = calculator.compute_chain_price_index(category_daily)

        if index_df.empty:
            logger.warning("指数计算失败")
            return

        # 5. 汇总指数
        aggregated_df = calculator.compute_aggregated_index(index_df, level='global')

        # 6. 合并结果
        if not aggregated_df.empty:
            result_df = index_df.merge(aggregated_df, on='date', how='left')
        else:
            result_df = index_df

        # 7. 保存结果
        output_path = f'{DATA_DIR}/price_index_results.csv'
        result_df.to_csv(output_path, index=False)
        logger.info(f"结果已保存: {output_path}")

        # 8. 可视化
        visualizer = Visualizer(client)
        visualizer.plot_price_index(
            result_df,
            title=f"高频电商价格指数趋势图 (基期: {BASE_DATE})",
            save_path=f'{DATA_DIR}/price_index_trend.png'
        )

        # 9. 输出统计
        logger.info("=" * 60)
        logger.info("计算完成!")
        logger.info(f"  日期范围: {result_df['date'].min()} 至 {result_df['date'].max()}")
        logger.info(f"  分类数: {result_df['category_id'].nunique()}")
        logger.info(f"  总记录: {len(result_df)}")
        if 'global_index' in result_df.columns:
            logger.info(f"  最新指数: {result_df['global_index'].iloc[-1]:.2f}")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"运行失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

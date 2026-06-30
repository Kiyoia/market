# main.py
"""主程序入口"""

import logging

from config import LOG_CONFIG
from pipeline import run_index_pipeline

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

    try:
        result_df = run_index_pipeline(save_chart=True)

        logger.info("=" * 60)
        logger.info("计算完成!")
        logger.info(f"  日期范围: {result_df['date'].min()} 至 {result_df['date'].max()}")
        logger.info(f"  分类数: {result_df['category_id'].nunique()}")
        logger.info(f"  总记录: {len(result_df)}")
        logger.info(f"  最新指数: {result_df['global_index'].dropna().iloc[-1]:.2f}")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"运行失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

"""主程序入口"""

import argparse
import logging
import traceback

from config import settings


def configure_logging() -> None:
    """配置日志输出"""
    logging.basicConfig(
        level=getattr(logging, settings.log.level.upper(), logging.INFO),
        format=settings.log.format,
    )


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="高频电商价格指数计算平台")
    parser.add_argument("-server", action="store_true", help="启动 API 服务")
    parser.add_argument("--host", help="API 服务监听地址，默认读取配置文件")
    parser.add_argument("--port", type=int, help="API 服务监听端口，默认读取配置文件")
    return parser.parse_args()


def run_pipeline() -> int:
    """执行价格指数计算流程"""
    from pipeline import run_index_pipeline

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
        return 0
    except Exception as exc:
        logger.error(f"运行失败: {exc}")
        traceback.print_exc()
        return 1


def run_server(host: str | None, port: int | None) -> int:
    """启动 API 服务"""
    import uvicorn

    from api import app

    bind_host = host or settings.server.host
    bind_port = port or settings.server.port
    logger.info(f"启动 API 服务: http://{bind_host}:{bind_port}")
    uvicorn.run(app, host=bind_host, port=bind_port)
    return 0


def main() -> int:
    """主函数"""
    args = parse_args()
    configure_logging()
    if args.server:
        return run_server(args.host, args.port)
    return run_pipeline()


if __name__ == "__main__":
    raise SystemExit(main())

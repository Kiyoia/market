# config.py
"""配置文件"""

# 数据库配置
CLICKHOUSE_CONFIG = {
    'host': 'cc-bp1n5v8x78h066754-ck-l3.clickhouseserver.rds.aliyuncs.com',
    'port': 9000,
    'database': 'default',
    'user': 'user1',
    'password': 'zH*$FRoEvGT$Vn36'
}

# 数据目录
DATA_DIR = './data'

# 基期日期
BASE_DATE = '2025-05-17'

# 异常检测参数
ANOMALY_PARAMS = {
    'std_threshold': 3,          # 标准差阈值
    'price_high_ratio': 5,       # 价格上限倍数
    'price_low_ratio': 0.2,      # 价格下限倍数
    'historical_window': 30,     # 历史窗口天数
    'min_category_count': 10     # 分类最少记录数
}

# 日志配置
LOG_CONFIG = {
    'level': 'INFO',
    'format': '%(asctime)s - %(levelname)s - %(message)s'
}
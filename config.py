"""应用配置加载模块"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ClickHouseConfig:
    """ClickHouse 连接配置"""

    host: str = ""
    port: int = 9000
    database: str = ""
    user: str = ""
    password: str = ""

    def validate(self) -> None:
        """校验数据库连接所需配置"""
        missing = [
            name
            for name, value in {
                "clickhouse.host": self.host,
                "clickhouse.database": self.database,
                "clickhouse.user": self.user,
                "clickhouse.password": self.password,
            }.items()
            if not str(value).strip()
        ]
        if missing:
            fields = "、".join(missing)
            raise ValueError(
                f"数据库配置不完整：{fields}。请复制 config.example.yaml 为 config.yaml，"
                "并填写 ClickHouse 连接信息。"
            )


@dataclass(frozen=True)
class AnomalyParams:
    """异常检测参数"""

    std_threshold: float = 3
    price_high_ratio: float = 5
    price_low_ratio: float = 0.2
    historical_window: int = 30
    min_category_count: int = 10


@dataclass(frozen=True)
class LogConfig:
    """日志配置"""

    level: str = "INFO"
    format: str = "%(asctime)s - %(levelname)s - %(message)s"


@dataclass(frozen=True)
class ServerConfig:
    """API 服务配置"""

    host: str = "127.0.0.1"
    port: int = 8000


@dataclass(frozen=True)
class SafetyConfig:
    """安全开关配置"""

    db_write_enabled: bool = False
    schema_reset_enabled: bool = False


@dataclass(frozen=True)
class AppConfig:
    """应用配置"""

    clickhouse: ClickHouseConfig = field(default_factory=ClickHouseConfig)
    data_dir: str = "./data"
    base_date: str = "2025-05-17"
    anomaly_params: AnomalyParams = field(default_factory=AnomalyParams)
    log: LogConfig = field(default_factory=LogConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)


def _runtime_dir() -> Path:
    """获取源码目录或 exe 所在目录"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _config_path() -> Path:
    """按运行位置查找配置文件"""
    runtime_dir = _runtime_dir()
    candidates = [
        Path.cwd() / "config.yaml",
        runtime_dir / "config.yaml",
        Path.cwd() / "config.example.yaml",
        runtime_dir / "config.example.yaml",
    ]
    for path in candidates:
        if path.exists():
            return path
    return runtime_dir / "config.example.yaml"


def _read_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 配置"""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误：{path}")
    return data


def load_config() -> AppConfig:
    """加载应用配置"""
    data = _read_yaml(_config_path())
    clickhouse = data.get("clickhouse") or {}
    anomaly_params = data.get("anomaly_params") or {}
    log = data.get("log") or {}
    server = data.get("server") or {}
    safety = data.get("safety") or {}

    return AppConfig(
        clickhouse=ClickHouseConfig(
            host=str(clickhouse.get("host", "") or ""),
            port=int(clickhouse.get("port", 9000) or 9000),
            database=str(clickhouse.get("database", "") or ""),
            user=str(clickhouse.get("user", "") or ""),
            password=str(clickhouse.get("password", "") or ""),
        ),
        data_dir=str(data.get("data_dir", "./data") or "./data"),
        base_date=str(data.get("base_date", "2025-05-17") or "2025-05-17"),
        anomaly_params=AnomalyParams(
            std_threshold=float(anomaly_params.get("std_threshold", 3)),
            price_high_ratio=float(anomaly_params.get("price_high_ratio", 5)),
            price_low_ratio=float(anomaly_params.get("price_low_ratio", 0.2)),
            historical_window=int(anomaly_params.get("historical_window", 30)),
            min_category_count=int(anomaly_params.get("min_category_count", 10)),
        ),
        log=LogConfig(
            level=str(log.get("level", "INFO") or "INFO"),
            format=str(
                log.get("format", "%(asctime)s - %(levelname)s - %(message)s")
                or "%(asctime)s - %(levelname)s - %(message)s"
            ),
        ),
        server=ServerConfig(
            host=str(server.get("host", "127.0.0.1") or "127.0.0.1"),
            port=int(server.get("port", 8000) or 8000),
        ),
        safety=SafetyConfig(
            db_write_enabled=bool(safety.get("db_write_enabled", False)),
            schema_reset_enabled=bool(safety.get("schema_reset_enabled", False)),
        ),
    )


settings = load_config()

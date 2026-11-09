# core/config_loader.py
import copy
import tomllib
from pathlib import Path
from core.logger import root_logger

# 项目根目录：本文件位于 <项目根>/core/config_loader.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 配置文件固定位置：<项目根>/config/config.toml
CONFIG_PATH = PROJECT_ROOT / "config" / "config.toml"

DEFAULT_CONFIG = {
    "ollama": {
        "chat_url": "http://localhost:11434/api/chat",
        "chat_model": "deepseekR1Q7B:latest",
        "vision_model": "qwen3.5:4b",
        "timeout": 120,
        "vision_timeout": 300
    },
    "models": {
        "main": "",
        "light": "",
        "vision": ""
    },
    "memory": {
        "short_max_token": 3000,
        "summary_max_token": 2000,
        "compress_target_len": 120,
        "summary_merge_len": 300,
        "score_threshold_save": 6.0,
        "score_threshold_archive": 2.0,
        "score_decay_rate": 0.02
    },
    "sandbox": {
        "root_path": str(PROJECT_ROOT / "sandbox")
    },
    "kb": {
        "persist_path": str(PROJECT_ROOT / "agent_memory_db")
    },
    "log": {
        "log_path": str(PROJECT_ROOT / "logs")
    },
    # 书库：本地电子书目录，默认为项目内空的 books/，首次使用请在面板「地址管理器」改成自己的目录
    "library": {
        "path": str(PROJECT_ROOT / "books")
    },
    "dpi": {
        "enable_dpi_fix": True
    },
    "vision_acl": {
        "max_scan_depth": 2
    },
    "log_level": "INFO"
}


def resolve_path(p: str) -> str:
    """把配置中的路径解析为绝对路径：相对路径基于项目根目录，而不是进程工作目录"""
    if not p:
        return p
    path = Path(p)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path.resolve())


def load_config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if not CONFIG_PATH.exists():
        root_logger.warning(f"config.toml not found at {CONFIG_PATH}, use default config")
    else:
        try:
            with open(CONFIG_PATH, "rb") as f:
                user_cfg = tomllib.load(f)
            # 递归合并用户配置到默认配置
            merge_dict(cfg, user_cfg)
            root_logger.info(f"Config loaded from {CONFIG_PATH}")
        except Exception as e:
            root_logger.error(f"Load config failed: {e}")

    # 所有路径字段统一解析为基于项目根目录的绝对路径
    for section, keys in (("sandbox", ("root_path",)), ("kb", ("persist_path",)),
                          ("log", ("log_path",)), ("library", ("path",))):
        if section in cfg:
            for key in keys:
                if key in cfg[section]:
                    cfg[section][key] = resolve_path(cfg[section][key])
    return cfg


def merge_dict(base: dict, new: dict):
    for k, v in new.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            merge_dict(base[k], v)
        else:
            base[k] = v


def save_config(cfg: dict = None):
    """把当前配置写回 config/config.toml（供dashboard地址管理器调用）"""
    cfg = cfg or config_loader
    # 托管段：由面板管理的固定段
    managed = ("ollama", "models", "memory", "sandbox", "kb", "log", "library", "dpi", "vision_acl")
    # 未托管段（session / idle / vision.rules / vision.addresses 等）原样保留，避免保存路径时被静默丢弃
    preserved: dict = {}
    try:
        with open(CONFIG_PATH, "rb") as f:
            old = tomllib.load(f)
        for k, v in old.items():
            if k not in managed and isinstance(v, dict):
                preserved[k] = v
    except Exception:
        preserved = {}
    lines = [
        "# ==============================",
        "# Agent全局配置文件",
        "# 👉 【地址管理器】dashboard面板可以可视化修改下面所有路径",
        "# ⚠️ 修改路径后必须重启程序才生效，原有数据不会自动迁移",
        "# =============================="
    ]
    for section in managed:
        if section not in cfg or not isinstance(cfg[section], dict):
            continue
        lines.append(f"\n[{section}]")
        for k, v in cfg[section].items():
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            else:
                # toml基本字符串中反斜杠必须转义（Windows路径），否则tomllib无法解析
                escaped = str(v).replace("\\", "\\\\")
                lines.append(f'{k} = "{escaped}"')
    # 保留原文件中未托管段（保持嵌套结构）
    for sec_name, sec_val in preserved.items():
        lines.append(f"\n[{sec_name}]")
        for k, v in sec_val.items():
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            elif isinstance(v, list):
                lines.append(f"{k} = [{_toml_array(v)}]")
            elif isinstance(v, dict):
                # 嵌套段（如 [vision.rules]、[vision.addresses]）
                lines.append(f"\n[{sec_name}.{k}]")
                for k2, v2 in v.items():
                    if isinstance(v2, bool):
                        lines.append(f"{k2} = {'true' if v2 else 'false'}")
                    elif isinstance(v2, (int, float)):
                        lines.append(f"{k2} = {v2}")
                    elif isinstance(v2, list):
                        lines.append(f"{k2} = [{_toml_array(v2)}]")
                    else:
                        escaped = str(v2).replace("\\", "\\\\")
                        lines.append(f'{k2} = "{escaped}"')
            else:
                escaped = str(v).replace("\\", "\\\\")
                lines.append(f'{k} = "{escaped}"')
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    root_logger.info(f"Config saved to {CONFIG_PATH}")


def _toml_array(items: list) -> str:
    """list → TOML 数组字面量（字符串元素转义反斜杠）"""
    parts = []
    for it in items:
        if isinstance(it, bool):
            parts.append("true" if it else "false")
        elif isinstance(it, (int, float)):
            parts.append(str(it))
        else:
            parts.append(f'"{str(it).replace(chr(92), chr(92) * 2)}"')
    return ", ".join(parts)


config_loader = load_config()

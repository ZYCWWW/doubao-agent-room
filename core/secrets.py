# core/secrets.py
"""密钥解析：环境变量 > config/secrets.toml > config.toml（旧明文，兼容一版）。

约定：
- config.toml 不再保存真实密钥；
- config/secrets.toml 已在 .gitignore 里，只留本机；
- 环境变量优先级最高，便于临时覆盖 / CI。
"""
import os
import tomllib

from core.config_loader import config_loader, PROJECT_ROOT

SECRETS_PATH = PROJECT_ROOT / "config" / "secrets.toml"

# 每个 (section, key) 额外接受的环境变量名（按优先级）
ENV_ALIASES = {
    ("external_llm", "api_key"): ("DEEPSEEK_API_KEY", "EXTERNAL_LLM_API_KEY"),
}


def get_secret(section: str, key: str = "api_key", default: str = "") -> str:
    """按 环境变量 → secrets.toml → config.toml → default 的顺序取密钥"""
    names = list(ENV_ALIASES.get((section, key), ())) + [f"{section.upper()}_{key.upper()}"]
    for name in names:
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()
    try:
        with open(SECRETS_PATH, "rb") as f:
            data = tomllib.load(f)
        val = (data.get(section) or {}).get(key)
        if val:
            return str(val).strip()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    try:
        val = (config_loader.get(section, {}) or {}).get(key)
        if val:
            return str(val).strip()
    except Exception:
        pass
    return default

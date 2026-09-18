"""配置层：端点档案与配置提供方的读写（纯数据，无 UI 依赖）。

默认目录为 ~/.config/clash-controller，可通过 -D/--dir 或环境变量覆盖。
"""
import json
import os
from collections.abc import Callable
from pathlib import Path

CONFIG_DIR_ENV = "CLASH_CONTROLLER_CONFIG_DIR"
DEFAULT_CONFIG_DIR = "~/.config/clash-controller"


def _noop(_message: str) -> None:
    pass


def get_config_dir() -> Path:
    return Path(os.environ.get(CONFIG_DIR_ENV, DEFAULT_CONFIG_DIR)).expanduser()


def get_profiles_path() -> Path:
    return get_config_dir() / "profiles.json"


def get_providers_path() -> Path:
    return get_config_dir() / "config_providers.json"


def _load_json(path: Path, log: Callable[[str], None]) -> list:
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log(f"无法读取或解析配置文件 {path}: {e}")
        return []
    return data if isinstance(data, list) else []


def _save_json(path: Path, data: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    return path


def load_profiles(log: Callable[[str], None] = _noop) -> list:
    return _load_json(get_profiles_path(), log)


def save_profiles(profiles: list) -> Path:
    return _save_json(get_profiles_path(), profiles)


def load_config_providers(log: Callable[[str], None] = _noop) -> list:
    return _load_json(get_providers_path(), log)


def save_config_providers(providers: list) -> Path:
    return _save_json(get_providers_path(), providers)

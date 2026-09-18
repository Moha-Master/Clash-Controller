"""配置提供方拉取、当前配置读取与比对辅助（纯服务层，无 UI）。"""
import hashlib
import os
from collections.abc import Callable
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from .ssh import fetch_remote_file_meta_and_content


def _noop(_message: str) -> None:
    pass


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_endpoint_type(url: str) -> str:
    """根据 URL 猜测端点类型：本地回环地址返回 local，否则 remote。"""
    try:
        host = (urlparse(url).hostname or '').lower()
        if host in ('127.0.0.1', 'localhost', '::1'):
            return 'local'
    except Exception:
        pass
    return 'remote'


def default_ssh_host(url: str) -> str:
    try:
        host = urlparse(url).hostname
        if host:
            return host
    except Exception:
        pass
    return '127.0.0.1'


def get_remote_last_modified(url: str) -> datetime | None:
    """读取远端 URL 的 Last-Modified 头，失败或缺失返回 None。"""
    try:
        response = requests.head(url, timeout=5)
        response.raise_for_status()
        last_modified = response.headers.get('Last-Modified')
        if last_modified:
            return parsedate_to_datetime(last_modified)
    except requests.exceptions.RequestException:
        return None
    return None


def fetch_remote_config(url: str) -> tuple[str, str, datetime | None]:
    """拉取远端配置，返回 (text, sha256, last_modified)。失败抛异常。"""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    text = response.text
    return text, compute_sha256(text.encode('utf-8')), get_remote_last_modified(url)


def read_current_config(
    profile: dict,
    log: Callable[[str], None] = _noop,
    debug: bool = False,
) -> tuple[int, bytes]:
    """读取目标端点当前的 config.yaml，返回 (mtime, bytes)。"""
    if profile.get('endpoint_type', 'local') == 'remote':
        return fetch_remote_file_meta_and_content(profile, log=log, debug=debug)

    config_dir = profile.get('config_directory', '/etc/clash')
    config_path = os.path.join(config_dir, 'config.yaml')
    if not os.path.exists(config_path):
        raise RuntimeError(f"local config file not found: {config_path}")
    with open(config_path, 'rb') as f:
        content = f.read()
    return int(os.path.getmtime(config_path)), content


def evaluate_update(remote_hash: str, remote_mod_time, local_hash: str, local_mtime: int) -> str:
    """比对远端与目标配置，返回更新决策。

    - ``auto``：远端较新且内容不同，自动更新
    - ``confirm_older``：内容不同但远端时间戳不更新，需确认
    - ``confirm_unknown``：内容不同且无法获取远端时间，需确认
    - ``confirm_same``：内容相同，需确认是否仍覆盖
    """
    if remote_hash != local_hash:
        if remote_mod_time is not None:
            if int(remote_mod_time.timestamp()) > local_mtime:
                return 'auto'
            return 'confirm_older'
        return 'confirm_unknown'
    return 'confirm_same'

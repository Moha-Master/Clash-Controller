"""配置提供方拉取、当前配置读取与比对辅助（纯服务层，无 UI）。"""
import hashlib
import os
from collections.abc import Callable
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from .platform_utils import default_local_config_dir, expand_local_path
from .ssh import fetch_remote_file_meta_and_content

CONFIG_ENCODINGS = ("utf-8-sig", "gb18030")


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


def decode_config_bytes(data: bytes) -> str:
    """把订阅 / 本地配置字节解码为文本。

    不能用 ``response.text``：requests 对没有 charset 的 ``text/*`` 会强制按
    ISO-8859-1 解码，中文会变成乱码。这里显式按 UTF-8（去 BOM）→ GB18030
    的顺序尝试，覆盖国内机场常见的两种编码；带 BOM 的 UTF-16（Windows 记事本
    “Unicode”格式）也能识别。
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        # Windows 记事本“Unicode”保存的是带 BOM 的 UTF-16。
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in CONFIG_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("配置内容不是有效的 UTF-8 / GB18030 文本，无法部署")


def normalize_config_text(text: str) -> str:
    """统一为 LF 换行，避免 Windows 文本模式写出的 CRLF 造成哈希反复不一致。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonical_config_bytes(data: bytes) -> bytes:
    """把配置规范化为 UTF-8（无 BOM、LF 换行）字节，用于落盘与哈希比对。"""
    return normalize_config_text(decode_config_bytes(data)).encode("utf-8")


def parse_last_modified(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None


def fetch_remote_config(url: str) -> tuple[str, str, datetime | None]:
    """拉取远端配置，返回 (规范化文本, sha256, last_modified)。失败抛异常。

    直接读取 ``response.content`` 并显式解码，最后统一换行；哈希基于规范化后的
    UTF-8 字节，保证与本地落盘内容可比。Last-Modified 取自本次 GET 响应，避免
    额外 HEAD 请求（部分订阅服务器不支持 HEAD）。
    """
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = canonical_config_bytes(response.content)
    text = payload.decode('utf-8')
    return text, compute_sha256(payload), parse_last_modified(response.headers.get('Last-Modified'))


def read_current_config(
    profile: dict,
    log: Callable[[str], None] = _noop,
    debug: bool = False,
) -> tuple[int, bytes]:
    """读取目标端点当前的 config.yaml，返回 (mtime, bytes)。"""
    if profile.get('endpoint_type', 'local') == 'remote':
        return fetch_remote_file_meta_and_content(profile, log=log, debug=debug)

    config_dir = expand_local_path(profile.get('config_directory') or default_local_config_dir())
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

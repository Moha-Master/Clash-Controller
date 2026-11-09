"""跨平台本地文件辅助：Clash 配置目录探测与 Windows UAC 提权写入。

只服务部署链路，不包含任何 Textual 依赖；失败统一抛 RuntimeError。
"""
import base64
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable

IS_WINDOWS = os.name == "nt"

_ELEVATION_TIMEOUT = 120.0
_WRITE_RETRIES = 3
_WRITE_RETRY_DELAY = 0.2


def _noop(_message: str) -> None:
    pass


def default_local_config_dir() -> str:
    """返回当前平台 Clash 核心常见的配置目录（config.yaml 所在目录）。"""
    if not IS_WINDOWS:
        return "/etc/clash"
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".config", "clash"),
        os.path.join(home, ".config", "mihomo"),
    ]
    existing = [path for path in candidates if os.path.isdir(path)]
    # 同时存在时优先选最近修改过 config.yaml 的那个，尽量贴近当前实际使用的核心。
    with_config = [path for path in existing if os.path.exists(os.path.join(path, "config.yaml"))]
    if with_config:
        return max(with_config, key=lambda path: _mtime_or_zero(os.path.join(path, "config.yaml")))
    if existing:
        return existing[0]
    return candidates[0]


def _mtime_or_zero(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def expand_local_path(path: str) -> str:
    """展开 ~ 与 %VAR% / $VAR%（Windows 两种变量写法都支持），并转为绝对路径。"""
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def write_local_config(
    config_dir: str,
    config_path: str,
    backup_path: str,
    payload: bytes,
    *,
    allow_elevation: bool,
    log: Callable[[str], None] = _noop,
) -> None:
    """原子写入本地 config.yaml；Windows 下遇到权限错误时按需弹 UAC。

    先尝试以当前用户权限写入（大多数 Windows 用户目录不需要提权）；只有真正
    遇到 PermissionError 且 ``allow_elevation`` 为真时才启动管理员进程。
    """
    try:
        _write_atomic(config_dir, config_path, backup_path, payload)
        _verify_written(config_path, payload)
        return
    except PermissionError as exc:
        if getattr(exc, "winerror", None) == 32:
            raise RuntimeError(
                f"目标文件正被其他程序占用，无法写入：{config_path}。请稍后重试。"
            ) from exc
        if not IS_WINDOWS:
            raise
        if not allow_elevation:
            raise RuntimeError(
                f"没有权限写入 {config_path}。"
                "若该目录受 UAC 保护，请在端点设置中开启“管理员写入 (UAC)”后重试。"
            ) from exc
        log(f"[UAC] 正在请求管理员权限写入 {config_path}")
        _elevated_write(config_path, backup_path, payload, log=log)
        if not _verify_written(config_path, payload, strict=False):
            log("[UAC] 写入完成，但当前用户无法读回配置进行校验（可能受 ACL 限制）")


def _write_atomic(config_dir: str, config_path: str, backup_path: str, payload: bytes) -> None:
    """写入同目录临时文件，备份后原子替换目标文件。"""
    os.makedirs(config_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".clashctl-", suffix=".tmp", dir=config_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not IS_WINDOWS:
            # mkstemp 默认 0600；保留原文件权限，避免 Clash 以其他用户运行时读不到。
            if os.path.exists(config_path):
                shutil.copymode(config_path, tmp_path)
            else:
                os.chmod(tmp_path, 0o644)
        if os.path.exists(config_path):
            shutil.copy2(config_path, backup_path)
        for attempt in range(_WRITE_RETRIES):
            try:
                os.replace(tmp_path, config_path)
                return
            except PermissionError as exc:
                # Windows 上目标文件可能恰好被内核读配置占用（WinError 32）。
                if getattr(exc, "winerror", None) != 32 or attempt == _WRITE_RETRIES - 1:
                    raise
                time.sleep(_WRITE_RETRY_DELAY * (attempt + 1))
    finally:
        _remove_quietly(tmp_path)


def _verify_written(config_path: str, payload: bytes, *, strict: bool = True) -> bool:
    try:
        with open(config_path, "rb") as handle:
            written = handle.read()
    except OSError as exc:
        if not strict:
            return False
        raise RuntimeError(f"写入后无法读取配置文件进行校验：{config_path}: {exc}") from exc
    if written != payload:
        raise RuntimeError("配置文件写入后校验失败（内容与预期不一致），请检查磁盘权限或杀毒软件拦截")
    return True


def _elevated_write(
    config_path: str,
    backup_path: str,
    payload: bytes,
    log: Callable[[str], None] = _noop,
) -> None:
    """通过 UAC 提权的隐藏 PowerShell 进程备份并覆盖配置文件。"""
    work_dir = tempfile.mkdtemp(prefix="clashctl-elev-")
    try:
        payload_path = os.path.join(work_dir, "payload.yaml")
        with open(payload_path, "wb") as handle:
            handle.write(payload)
        status_path = os.path.join(work_dir, "status.txt")
        script = _build_elevation_script(config_path, backup_path, payload_path, status_path)
        _launch_elevated_powershell(script, work_dir)
        _wait_for_elevation_status(status_path, log=log)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _build_elevation_script(
    target_path: str,
    backup_path: str,
    payload_path: str,
    status_path: str,
) -> str:
    """生成提权脚本。

    路径以 Base64 传入，避免中文用户名、空格、单引号破坏 PowerShell 语法。
    """
    target = _encode_ps_arg(target_path)
    backup = _encode_ps_arg(backup_path)
    payload = _encode_ps_arg(payload_path)
    status = _encode_ps_arg(status_path)
    return f"""$ErrorActionPreference = 'Stop'
function Decode([string] $value) {{
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($value))
}}
$target = Decode '{target}'
$backup = Decode '{backup}'
$payload = Decode '{payload}'
$status = Decode '{status}'
$statusTmp = $status + '.tmp'
try {{
    $directory = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $directory)) {{
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }}
    $staging = Join-Path $directory ('.clashctl-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    Copy-Item -LiteralPath $payload -Destination $staging -Force
    if (Test-Path -LiteralPath $target) {{
        Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        try {{
            [IO.File]::Replace($staging, $target, $backup, $true)
        }} catch {{
            Copy-Item -LiteralPath $target -Destination $backup -Force
            Copy-Item -LiteralPath $staging -Destination $target -Force
            Remove-Item -LiteralPath $staging -Force -ErrorAction SilentlyContinue
        }}
    }} else {{
        Move-Item -LiteralPath $staging -Destination $target -Force
    }}
    Set-Content -LiteralPath $statusTmp -Value 'OK' -Encoding Ascii
    Move-Item -LiteralPath $statusTmp -Destination $status -Force
}} catch {{
    Remove-Item -LiteralPath $staging -Force -ErrorAction SilentlyContinue
    Set-Content -LiteralPath $statusTmp -Value ('ERR ' + $_.Exception.Message) -Encoding UTF8
    Move-Item -LiteralPath $statusTmp -Destination $status -Force
}}
"""


def _encode_ps_arg(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _launch_elevated_powershell(script: str, work_dir: str) -> None:
    import ctypes

    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(powershell):
        powershell = "powershell.exe"
    # 用 -EncodedCommand 传脚本：不落 .ps1 文件，也不受脚本执行策略 / BOM 影响。
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    params = subprocess.list2cmdline(["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    shell32.ShellExecuteW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    result = shell32.ShellExecuteW(None, "runas", powershell, params, work_dir, 0)
    value = int(result or 0)
    if value <= 32:
        raise RuntimeError(f"未获得管理员授权（UAC 被取消或拒绝），配置未写入。（ShellExecuteW={value}）")


def _wait_for_elevation_status(status_path: str, log: Callable[[str], None] = _noop) -> None:
    deadline = time.monotonic() + _ELEVATION_TIMEOUT
    while time.monotonic() < deadline:
        if os.path.exists(status_path):
            try:
                with open(status_path, encoding="utf-8-sig") as handle:
                    status = handle.read().strip()
            except OSError:
                status = ""
            if status:
                if status == "OK":
                    return
                message = status.removeprefix("ERR").strip()
                raise RuntimeError(f"管理员写入失败：{message}")
        time.sleep(0.2)
    log("[UAC] 等待管理员写入超时")
    raise RuntimeError(
        "等待管理员写入超时；请确认 UAC 提示已被处理，或关闭占用配置文件的程序后重试。"
    )


def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass

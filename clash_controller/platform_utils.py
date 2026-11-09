"""跨平台本地文件辅助：Clash 配置目录探测、UAC 状态判定与提权写入。

只服务部署链路，不包含任何 Textual 依赖；失败统一抛 RuntimeError。
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable

IS_WINDOWS = os.name == "nt"

# 提权写入的等待上限。有进程句柄时靠「进程退出 + 目标文件内容」判定，_HELPER_TIMEOUT 只是
# 保险（用户在 UAC 上的节奏不可控，给足时间）；万一拿不到句柄，只能靠状态文件/内容轮询，
# 用较短的 _STATUS_TIMEOUT。
_HELPER_TIMEOUT = 900.0
_STATUS_TIMEOUT = 120.0
_POLL_INTERVAL = 0.3
# 订阅配置可能有上百 KB，等内容校验不必每轮都做（提权进程通常一秒内就结束）。
_CONTENT_POLL = 2.0
_HEARTBEAT = 15.0
_WRITE_RETRIES = 3
_WRITE_RETRY_DELAY = 0.2
_STATUS_WRITES = 3
_STATUS_RETRY_DELAY = 0.2

# ShellExecuteEx：SEE_MASK_NOCLOSEPROCESS 拿子进程句柄，SEE_MASK_NOASYNC 让调用同步走完
# 授权流程（否则 UAC 还没确认就返回，超时计时会从弹框之前开始算）。
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SEE_MASK_NOASYNC = 0x00000100
_SW_HIDE = 0
_WAIT_OBJECT_0 = 0
_STILL_ACTIVE = 259

# Win32 错误码
_ERROR_CANCELLED = 1223
_ERROR_ACCESS_DENIED = 5
_ERROR_SHARING_VIOLATION = 32
_ERROR_LOCK_VIOLATION = 33

# OpenProcessToken / GetTokenInformation
_TOKEN_QUERY = 0x0008
_TOKEN_ELEVATION_TYPE = 18  # TOKEN_INFORMATION_CLASS 里排在 TokenOrigin(17) 之后
_TOKEN_ELEVATION = 20
_ELEVATION_TYPE_DEFAULT = 1
_ELEVATION_TYPE_FULL = 2
_ELEVATION_TYPE_LIMITED = 3

# 提权能力（elevation_state 的返回值）
ELEVATION_POSSIBLE = "possible"
ELEVATION_ALREADY = "already"
ELEVATION_NOT_ADMIN = "not_admin"
ELEVATION_UNKNOWN = "unknown"

_ELEVATION_TEXT = {
    ELEVATION_POSSIBLE: "管理员账户（当前是受限令牌），可以通过 UAC 提权",
    ELEVATION_ALREADY: "当前进程已具备管理员权限",
    ELEVATION_NOT_ADMIN: "当前账户不是管理员组成员，本机无法直接提权",
    ELEVATION_UNKNOWN: "无法读取令牌信息",
}

# 提权 helper：由我们自己的包在管理员令牌下执行写入（见 elevated_helper 模块）
HELPER_MODULE = "clash_controller.elevated_helper"
HELPER_FLAG = "--elevated-write"

_SHELL32 = None
_KERNEL32 = None
_ADVAPI32 = None


class _SHELLEXECUTEINFOW(ctypes.Structure):
    """对应 shellapi.h 的 SHELLEXECUTEINFOW（x64 下 sizeof = 112）。

    DWORD / ULONG 一律用 ``c_uint32`` 而不是 ``c_ulong``：后者在 Linux 上是 8 字节，
    会让结构体布局与 Windows 不一致（也就无法在开发机上验证字段偏移）。
    """

    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        ("hIcon", ctypes.c_void_p),  # union { hIcon; hMonitor; }
        ("hProcess", ctypes.c_void_p),
    ]


def _noop(_message: str) -> None:
    pass


class ConfigWriteRefused(RuntimeError):
    """发布目标文件被拒（Windows 专属）：``kind`` 为 ``denied`` / ``occupied``。

    不把 Win32 错误码塞进 ``PermissionError.winerror`` —— 那个属性只在 Windows 上存在，
    开发机上无法验证判定分支，所以这里用显式类型承载。
    """

    def __init__(self, kind: str, path: str, winerror: int):
        super().__init__(f"{_win_error_text(winerror)}：{path}")
        self.kind = kind
        self.path = path
        self.winerror = winerror


REFUSAL_DENIED = "denied"
REFUSAL_OCCUPIED = "occupied"


def _refusal_kind(exc: BaseException) -> str | None:
    """把各种写入失败归一成 denied / occupied / None（None 表示不是权限类失败）。"""
    if isinstance(exc, ConfigWriteRefused):
        return exc.kind
    if isinstance(exc, PermissionError):
        if getattr(exc, "winerror", None) in (_ERROR_SHARING_VIOLATION, _ERROR_LOCK_VIOLATION):
            return REFUSAL_OCCUPIED
        return REFUSAL_DENIED
    return None


# -------------------------------------------------------------------------- 路径


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


# ---------------------------------------------------------------------- 令牌状态


def elevation_state() -> str:
    """返回当前进程的 UAC 提权能力，用来决定该不该弹框、以及失败时怎么解释。

    依据 ``GetTokenInformation`` 的两项（微软文档口径）：

    - ``TokenElevationTypeLimited``：管理员组成员 + UAC 过滤令牌 —— 唯一「点一下就能提权」的状态。
    - ``TokenElevationTypeFull``：已经是提升后的令牌。
    - ``TokenElevationTypeDefault``：没有链接令牌 —— 要么是本机 UAC 关闭（此时进程直接就是管理员），
      要么是标准用户；再用 ``TokenElevation.TokenIsElevated`` 把两者区分开。
    """
    if not IS_WINDOWS:
        return ELEVATION_UNKNOWN
    advapi32 = _advapi32()
    token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(ctypes.c_void_p(-1), _TOKEN_QUERY, ctypes.byref(token)):
        return ELEVATION_UNKNOWN
    try:
        kind = _token_uint(advapi32, token, _TOKEN_ELEVATION_TYPE)
        if kind == _ELEVATION_TYPE_LIMITED:
            return ELEVATION_POSSIBLE
        if kind == _ELEVATION_TYPE_FULL:
            return ELEVATION_ALREADY
        if kind == _ELEVATION_TYPE_DEFAULT:
            elevated = _token_uint(advapi32, token, _TOKEN_ELEVATION)
            return ELEVATION_ALREADY if elevated else ELEVATION_NOT_ADMIN
        return ELEVATION_UNKNOWN
    finally:
        _kernel32().CloseHandle(token)


def describe_elevation(state: str) -> str:
    return _ELEVATION_TEXT.get(state, state)


def _token_uint(advapi32, token, info_class: int) -> int | None:
    """读一项 DWORD 尺寸的令牌信息（TokenElevationType / TokenElevation 都是单个数值）。"""
    value = ctypes.c_uint32()
    returned = ctypes.c_uint32()
    ok = advapi32.GetTokenInformation(
        token, info_class, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(returned)
    )
    return int(value.value) if ok else None


# ------------------------------------------------------------------------ 写入


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
    遇到 PermissionError 才根据令牌状态决定「弹 UAC / 直接说明原因」。
    """
    try:
        _write_atomic(config_dir, config_path, backup_path, payload, log=log)
        _verify_written(config_path, payload)
        return
    except (PermissionError, ConfigWriteRefused) as exc:
        if _refusal_kind(exc) == REFUSAL_OCCUPIED:
            raise RuntimeError(
                f"目标文件正被其他程序占用，无法写入：{config_path}。请停止 Clash 核心或关闭"
                "正在编辑该文件的程序后重试。"
            ) from exc
        if not IS_WINDOWS:
            raise
        state = elevation_state()
        log(f"[UAC] 提权状态：{describe_elevation(state)}")
        if state == ELEVATION_ALREADY:
            raise RuntimeError(
                f"当前进程已经是管理员权限，写入 {config_path} 失败并不是权限不足："
                "请检查文件的只读属性、ACL 是否显式拒绝，或是否有程序占用它。"
            ) from exc
        if state == ELEVATION_NOT_ADMIN:
            raise RuntimeError(
                f"当前账户不是管理员组成员，无法通过 UAC 写入 {config_path}。"
                "建议把 Clash 配置目录改到用户目录（例如 %USERPROFILE%\\.config\\clash），"
                "或请管理员完成本次部署。"
            ) from exc
        if not allow_elevation:
            raise RuntimeError(
                f"没有权限写入 {config_path}。"
                "若该目录受 UAC 保护，请在端点设置中开启“管理员写入 (UAC)”后重试。"
            ) from exc
        log("[UAC] 请在弹出的 UAC 对话框中点击“是”")
        log(f"目标文件：{config_path}")
        _elevated_write(config_path, backup_path, payload, log=log)


def _write_atomic(
    config_dir: str,
    config_path: str,
    backup_path: str,
    payload: bytes,
    log: Callable[[str], None] = _noop,
) -> None:
    """写入同目录临时文件，再原子发布为目标文件（Windows 走 ReplaceFileW 保留 ACL）。"""
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
        for attempt in range(_WRITE_RETRIES):
            try:
                _publish(tmp_path, config_path, backup_path, log=log)
                return
            except (PermissionError, ConfigWriteRefused) as exc:
                # 目标被内核读配置占用（WinError 32/33）时才重试，其它权限错误直接上抛。
                if _refusal_kind(exc) != REFUSAL_OCCUPIED or attempt == _WRITE_RETRIES - 1:
                    raise
                time.sleep(_WRITE_RETRY_DELAY * (attempt + 1))
    finally:
        _remove_quietly(tmp_path)


def _publish(tmp_path: str, config_path: str, backup_path: str, log: Callable[[str], None] = _noop) -> None:
    """把临时文件发布成正式文件，并留下备份。

    Windows 用 ``ReplaceFileW``：它是同卷的文件系统级替换，会保留目标原有的 DACL、创建时间等
    属性；``os.replace`` 底层是 ``MoveFileExW``，只带走源文件的 ACL，等于静默改掉 config.yaml
    的权限。其它平台 ``os.replace`` 本身就是原子且保留 inode 权限的。
    """
    if IS_WINDOWS and os.path.exists(config_path):
        error = _replace_file_win(tmp_path, config_path, backup_path)
        if error is None:
            return
        if error == _ERROR_ACCESS_DENIED:
            raise ConfigWriteRefused(REFUSAL_DENIED, config_path, error)
        if error in (_ERROR_SHARING_VIOLATION, _ERROR_LOCK_VIOLATION):
            raise ConfigWriteRefused(REFUSAL_OCCUPIED, config_path, error)
        # 1175/1176/1177 之类：ReplaceFile 没做成，退回普通替换保证部署能完成。
        log(f"[写入] ReplaceFileW 未成功（WinError={error}），改用普通替换")
    if os.path.exists(config_path):
        shutil.copy2(config_path, backup_path)
    os.replace(tmp_path, config_path)


def _verify_written(config_path: str, payload: bytes) -> None:
    """直接写入后的校验：内容必须与预期一致。"""
    outcome = _target_outcome(config_path, payload)
    if outcome == "match":
        return
    if outcome == "unreadable":
        raise RuntimeError(f"写入后无法读取配置文件进行校验：{config_path}")
    raise RuntimeError("配置文件写入后校验失败（内容与预期不一致），请检查磁盘权限或杀毒软件拦截")


# -------------------------------------------------------------------- 提权写入


def _elevated_write(
    config_path: str,
    backup_path: str,
    payload: bytes,
    log: Callable[[str], None] = _noop,
) -> None:
    """以管理员权限再启动一个本包进程，由它完成备份与覆盖。"""
    work_dir = tempfile.mkdtemp(prefix="clashctl-elev-")
    process = None
    try:
        payload_path = os.path.join(work_dir, "payload.yaml")
        with open(payload_path, "wb") as handle:
            handle.write(payload)
        status_path = os.path.join(work_dir, "status.txt")
        program, params = _build_elevated_command(payload_path, config_path, backup_path, status_path)
        process = _launch_elevated(program, params, work_dir)
        if process is None:
            log("[UAC] 未取得提权进程句柄，改用状态文件与文件内容轮询判定")
        else:
            log("[UAC] 已获授权，正在由管理员进程写入配置")
        _await_elevation(process, status_path, config_path, payload, log=log)
    finally:
        if process:
            _close_handle(process)
        shutil.rmtree(work_dir, ignore_errors=True)


def _build_elevated_command(payload_path: str, target: str, backup: str, status: str) -> tuple[str, str]:
    """拼提权命令行：让管理员令牌下的**我们自己**做写入。

    不用 PowerShell：``-NoProfile -NonInteractive -EncodedCommand <base64>`` + 隐藏窗口正是
    终端安全软件重点盯的攻击特征（T1059.001 一类规则），可能允许启动却在最后一步拦掉写入；
    改跑自己的 Python 后父→子只是 ``python.exe → python.exe``，参数全是普通 argv（不经 shell），
    还能直接复用 POSIX 那套原子写入代码。打包成 exe（frozen）时由入口的 ``--elevated-write`` 分派。
    """
    if getattr(sys, "frozen", False):
        args = [HELPER_FLAG, payload_path, target, backup, status]
    else:
        args = ["-m", HELPER_MODULE, payload_path, target, backup, status]
    return sys.executable, subprocess.list2cmdline(args)


def elevated_apply_payload(payload_path: str, target_path: str, backup_path: str) -> None:
    """提权进程的实际工作：读 payload，原子写入目标（Windows 保留目标 ACL）。"""
    with open(payload_path, "rb") as handle:
        payload = handle.read()
    config_dir = os.path.dirname(target_path) or "."
    try:
        _write_atomic(config_dir, target_path, backup_path, payload)
    except OSError as exc:
        # 失败原因要能在主进程里读得懂：临时文件名对用户没有意义，补上目标路径。
        raise RuntimeError(f"无法写入目标配置 {target_path}：{exc}") from exc


def write_status(status_path: str, text: str) -> bool:
    """供提权 helper 回写结果。

    写失败也没关系：调用方最终以目标文件内容判定成败，状态文件只是用来带回失败原因。
    """
    for attempt in range(_STATUS_WRITES):
        try:
            with open(status_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            return True
        except OSError:
            if attempt == _STATUS_WRITES - 1:
                return False
            time.sleep(_STATUS_RETRY_DELAY)
    return False


def _launch_elevated(program: str, params: str, work_dir: str):
    """以管理员权限启动 program（隐藏窗口），返回进程句柄（拿不到时返回 None）。"""
    shell32 = _shell32()
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = program
    info.lpParameters = params
    info.lpDirectory = work_dir
    info.nShow = _SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = _last_win_error()
        app_code = int(info.hInstApp or 0)
        if error == _ERROR_CANCELLED or app_code == _ERROR_ACCESS_DENIED:
            raise RuntimeError("未获得管理员授权（UAC 被取消或拒绝），配置未写入。")
        raise RuntimeError(f"启动管理员写入进程失败（WinError={error}, ShellExecute={app_code}）。")
    return int(info.hProcess or 0) or None


def _await_elevation(
    process,
    status_path: str,
    config_path: str,
    payload: bytes,
    log: Callable[[str], None] = _noop,
) -> None:
    """等待提权写入完成。

    成功与否**以磁盘上的目标内容为准**，状态文件只用来取失败原因：提权进程可能被安全软件
    在最后一步拦掉、或状态文件写不出来，而 config.yaml 其实已经替换成功（早期版本因此把
    「已经成功」误报成「等待超时」）。有进程句柄时用它判断结束，不必空等到超时。
    """
    limit = _HELPER_TIMEOUT if process else _STATUS_TIMEOUT
    started = time.monotonic()
    deadline = started + limit
    next_content_check = 0.0
    next_heartbeat = started + _HEARTBEAT
    while True:
        status = _read_status(status_path)
        if status.startswith("ERR"):
            raise RuntimeError(f"管理员写入失败：{status.removeprefix('ERR').strip()}")

        now = time.monotonic()
        if now >= next_content_check:
            next_content_check = now + _CONTENT_POLL
            if _judge(status, config_path, payload, log=log):
                return

        if process:
            if _wait_for_exit(process, _POLL_INTERVAL):
                # 状态文件可能刚好在写入，稍等后再判一次收尾。
                time.sleep(_POLL_INTERVAL)
                if _judge(_read_status(status_path), config_path, payload, log=log):
                    return
                _raise_helper_failed(process, config_path)
        else:
            time.sleep(_POLL_INTERVAL)

        now = time.monotonic()
        if now > deadline:
            log("[UAC] 等待管理员写入超时")
            raise RuntimeError(
                "等待管理员写入超时；若刚才已允许 UAC，配置可能已经写入，请重新部署一次确认，"
                "或检查是否有安全软件拦截了提权进程。"
            )
        if now >= next_heartbeat:
            next_heartbeat = now + _HEARTBEAT
            log(f"[UAC] 仍在等待管理员写入完成（已 {int(now - started)} 秒）")


def _judge(status: str, config_path: str, payload: bytes, log: Callable[[str], None] = _noop) -> bool:
    """能否据状态与磁盘内容判定写入成功；回报失败则直接抛异常。"""
    if status.startswith("ERR"):
        raise RuntimeError(f"管理员写入失败：{status.removeprefix('ERR').strip()}")
    outcome = _target_outcome(config_path, payload)
    if outcome == "match":
        if not status:
            log("[UAC] 提权进程未写出确认文件，但 config.yaml 内容已更新，按写入成功处理")
        return True
    if status == "OK":
        if outcome == "mismatch":
            raise RuntimeError("管理员写入回报成功，但读回的 config.yaml 内容不一致，请确认是否被其他程序改写")
        log("[UAC] 写入完成，但当前用户无法读回配置进行校验（可能受 ACL 限制）")
        return True
    return False


def _raise_helper_failed(process, config_path: str) -> None:
    """提权进程已退出但无法确认成功：按退出码给出可操作的提示。"""
    code = _exit_code(process)
    if code not in (None, 0):
        raise RuntimeError(
            f"管理员写入进程异常退出（退出码 {code}），config.yaml 未被更新；"
            "请检查安全软件是否拦截了提权进程。"
        )
    raise RuntimeError(
        f"管理员写入进程已结束，但 {config_path} 内容未更新且没有失败详情；"
        "请确认目录权限，或先关闭正在读取 config.yaml 的程序后重试。"
    )


def _read_status(status_path: str) -> str:
    try:
        with open(status_path, encoding="utf-8-sig") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _target_outcome(config_path: str, payload: bytes) -> str:
    """返回 match / mismatch / unreadable（只读判定，不抛异常）。"""
    try:
        with open(config_path, "rb") as handle:
            written = handle.read()
    except OSError:
        return "unreadable"
    return "match" if written == payload else "mismatch"


# ---------------------------------------------------------------------- Win32


def _shell32():
    global _SHELL32
    if _SHELL32 is None:
        _SHELL32 = ctypes.WinDLL("shell32", use_last_error=True)
        _SHELL32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
        _SHELL32.ShellExecuteExW.restype = ctypes.c_int
    return _SHELL32


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is None:
        _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _KERNEL32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        _KERNEL32.WaitForSingleObject.restype = ctypes.c_uint32
        _KERNEL32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        _KERNEL32.GetExitCodeProcess.restype = ctypes.c_int
        _KERNEL32.CloseHandle.argtypes = [ctypes.c_void_p]
        _KERNEL32.ReplaceFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        _KERNEL32.ReplaceFileW.restype = ctypes.c_int
    return _KERNEL32


def _advapi32():
    global _ADVAPI32
    if _ADVAPI32 is None:
        _ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)
        _ADVAPI32.OpenProcessToken.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        _ADVAPI32.OpenProcessToken.restype = ctypes.c_int
        _ADVAPI32.GetTokenInformation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        _ADVAPI32.GetTokenInformation.restype = ctypes.c_int
    return _ADVAPI32


def _replace_file_win(replacement: str, target: str, backup: str) -> int | None:
    """ReplaceFileW：成功返回 None，失败返回 Win32 错误码（不抛异常，由调用方决定退路）。"""
    kernel32 = _kernel32()
    if kernel32.ReplaceFileW(target, replacement, backup, 0, None, None):
        return None
    return _last_win_error()


def _wait_for_exit(process: int, timeout: float) -> bool:
    kernel32 = _kernel32()
    return kernel32.WaitForSingleObject(ctypes.c_void_p(process), int(timeout * 1000)) == _WAIT_OBJECT_0


def _exit_code(process: int) -> int | None:
    kernel32 = _kernel32()
    code = ctypes.c_uint32()
    if not kernel32.GetExitCodeProcess(ctypes.c_void_p(process), ctypes.byref(code)):
        return None
    return None if code.value == _STILL_ACTIVE else int(code.value)


def _close_handle(process: int) -> None:
    try:
        _kernel32().CloseHandle(ctypes.c_void_p(process))
    except Exception:  # noqa: BLE001  # 收尾句柄关闭失败不值得影响主流程
        pass


def _last_win_error() -> int:
    """取 Win32 线程错误码（``use_last_error=True`` 时由 ctypes 保存，非 Windows 返回 0）。"""
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter else 0


_WIN_ERROR_TEXT = {
    _ERROR_ACCESS_DENIED: "拒绝访问",
    _ERROR_SHARING_VIOLATION: "文件正被其他进程占用",
    _ERROR_LOCK_VIOLATION: "文件被其他进程锁定",
    _ERROR_CANCELLED: "操作被取消",
}


def _win_error_text(code: int) -> str:
    return _WIN_ERROR_TEXT.get(code, f"Win32 错误 {code}")


def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass

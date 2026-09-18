"""Textual 应用入口：全局档案 / 客户端、状态栏、日志与启动流程。"""
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from .api import ClashAPI


class StatusBar(Static):
    """底部状态栏：展示最近一次操作结果 / 进行中提示。"""


class ClashControllerApp(App):
    """Clash 管理 TUI。"""

    CSS_PATH = Path(__file__).parent / "app.tcss"
    TITLE = "Clash Controller"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出", priority=True),
    ]

    def __init__(self, debug: bool = False) -> None:
        super().__init__()
        self.debug_mode = bool(debug)
        self.profile: dict | None = None
        self._api: ClashAPI | None = None
        self.app_logs: list[str] = []

    # ------------------------------------------------------------ 日志

    def add_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.app_logs.append(f"[{timestamp}] {message}")

    # ------------------------------------------------------------ 档案 / 客户端

    def set_profile(self, profile: dict) -> None:
        self.profile = profile
        self._api = None

    def clear_profile(self) -> None:
        self.profile = None
        self._api = None

    def get_api(self) -> ClashAPI:
        if self.profile is None:
            raise RuntimeError("尚未选择端点档案")
        if self._api is None:
            debug = self.debug_mode or bool(self.profile.get("debug_ssh"))
            self._api = ClashAPI(
                base_url=self.profile["url"],
                secret=self.profile.get("secret"),
                working_directory=self.profile.get("config_directory"),
                debug=debug,
                log=self.add_log,
            )
        return self._api

    # ------------------------------------------------------------ 状态栏 / 提示

    def compose(self) -> ComposeResult:
        yield StatusBar("", id="status")

    def status(self, message: str, kind: str = "") -> None:
        try:
            bar = self.query_one("#status", StatusBar)
        except Exception:
            return
        bar.update_classes({"ok": kind == "ok", "warn": kind == "warn", "err": kind == "err"})
        bar.update(message)

    def notify_ok(self, message: str) -> None:
        self.status(message, "ok")
        self.notify(message, severity="success", timeout=4)

    def notify_err(self, message: str) -> None:
        self.status(message, "err")
        self.notify(message, severity="error", timeout=8)

    def notify_warn(self, message: str) -> None:
        self.status(message, "warn")
        self.notify(message, severity="warning", timeout=5)

    def report(self, action_desc: str, result, error) -> str:
        """统一解释 ClashAPI 结果并反馈到状态栏 / 通知 / 日志，返回严重级别。"""
        from .api import interpret_api_result

        message, severity = interpret_api_result(action_desc, result, error)
        self.add_log(message)
        if self.debug_mode:
            self.add_log(f"[DEBUG] result={result!r} error={error!r}")
        if severity == "ok":
            self.notify_ok(message)
        elif severity == "warn":
            self.notify_warn(message)
        else:
            self.notify_err(message)
        return severity

    # ------------------------------------------------------------ 启动

    def on_mount(self) -> None:
        self.add_log("Application started.")
        self._bootstrap()

    @work(exclusive=True)
    async def _bootstrap(self) -> None:
        from .screens.profiles import ProfileListScreen

        self.push_screen(ProfileListScreen())

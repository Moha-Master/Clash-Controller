"""设置屏：TUN / 模式即时生效控件（无保存动作）、重载与升级操作；日志入口在顶栏折叠菜单。"""
import asyncio

from textual import on, work
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Select, Static, Switch

from ..ui import PageScreen
from ..widgets import ConfirmModal

MODES = [("规则", "rule"), ("全局", "global"), ("直连", "direct")]


class SettingsScreen(PageScreen):
    TITLE = "设置"
    HINT = "Tab 轮切焦点 · 回车 操作 · Ctrl+R 刷新状态 · Esc/Ctrl+C 返回"

    def __init__(self) -> None:
        super().__init__()
        self._tun = False
        self._mode = "rule"
        self.menu = [("查看应用日志", self._open_logs, False)]

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="kv-row"):
                yield Static("TUN", classes="kv-label")
                yield Switch(id="st-tun", classes="kv-ctl")
            with Horizontal(classes="kv-row gap-top"):
                yield Static("模式", classes="kv-label")
                yield Select(MODES, value="rule", allow_blank=False, compact=True, id="st-mode", classes="kv-ctl")
        with Horizontal(classes="filter-row gap-top"):
            yield Static("核心操作", classes="fl-label")
            yield Button("重载 GEO", id="reload-geo", compact=True)
        with Horizontal(classes="filter-row gap-top"):
            yield Static("重启升级", classes="fl-label")
            yield Button("重启 Clash", id="restart", variant="error", compact=True)
            yield Button("升级内核", id="up-kernel", compact=True)
            yield Button("升级 UI", id="up-ui", compact=True)
            yield Button("升级 GEO", id="up-geo", compact=True)

    def on_mount(self) -> None:
        self.set_subtitle((self.app.profile or {}).get("name", ""))
        self._load_configs()

    def _open_logs(self) -> None:
        from .logs import LogScreen

        self.app.push_screen(LogScreen())

    # ------------------------------------------------------------ 状态加载

    @work(exclusive=True)
    async def _load_configs(self) -> None:
        try:
            configs, error = await asyncio.to_thread(self.app.get_api().get_configs)
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"获取设置失败: {type(e).__name__}: {e}")
            return
        if error:
            self.app.notify_err(f"获取设置失败: {error}")
            return
        configs = configs if isinstance(configs, dict) else {}
        tun_cfg = configs.get("tun") or {}
        self._tun = bool(tun_cfg.get("enable", False)) if isinstance(tun_cfg, dict) else False
        mode = str(configs.get("mode", "rule"))
        self._mode = mode if mode in ("rule", "global", "direct") else "rule"
        self._sync_controls()

    def _sync_controls(self) -> None:
        """把内存态写回控件；先改状态再改控件，回写触发的 Changed 事件会被去重吞掉。"""
        self.query_one("#st-tun", Switch).value = self._tun
        self.query_one("#st-mode", Select).value = self._mode

    def reload_page(self) -> None:
        self._load_configs()

    # ------------------------------------------------------------ 即时操作（变更即生效）

    @on(Switch.Changed, "#st-tun")
    def _on_tun_changed(self, event: Switch.Changed) -> None:
        if bool(event.value) == self._tun:
            return
        self._tun = bool(event.value)
        self._call("toggle_tun", f"{'启用' if self._tun else '禁用'} TUN", self._tun)

    @on(Select.Changed, "#st-mode")
    def _on_mode_changed(self, event: Select.Changed) -> None:
        if event.value is Select.NULL:
            return
        mode = str(event.value)
        if mode == self._mode:
            return
        self._mode = mode
        labels = dict(MODES)
        self._call("set_mode", f"切换模式为 {labels.get(mode, mode)}", mode)

    @on(Button.Pressed, "#reload-geo")
    def _on_reload_geo(self) -> None:
        self._call("reload_geo_databases", "重载 GEO 数据库")

    @on(Button.Pressed, "#restart")
    def _on_restart(self) -> None:
        self._confirm_then("确定要重启 Clash Core 吗？", "restart", "重启 Clash Core")

    @on(Button.Pressed, "#up-kernel")
    def _on_up_kernel(self) -> None:
        self._confirm_then("确定要请求升级内核吗？", "upgrade_kernel", "升级内核")

    @on(Button.Pressed, "#up-ui")
    def _on_up_ui(self) -> None:
        self._confirm_then("确定要请求升级 UI 吗？", "upgrade_ui", "升级 UI")

    @on(Button.Pressed, "#up-geo")
    def _on_up_geo(self) -> None:
        self._confirm_then("确定要请求升级 GEO 数据库吗？", "upgrade_geo_databases", "升级 GEO 数据库")

    def _confirm_then(self, message: str, method_name: str, desc: str) -> None:
        def confirm(ok: bool) -> None:
            if ok:
                self._call(method_name, desc)

        self.app.push_screen(ConfirmModal(message, default_yes=False), confirm)

    @work(exclusive=True)
    async def _call(self, method_name: str, desc: str, *args) -> None:
        api = self.app.get_api()
        method = getattr(api, method_name)
        self.app.status(f"正在{desc}…")
        try:
            result, error = await asyncio.to_thread(method, *args)
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"{desc}失败: {type(e).__name__}: {e}")
            self._load_configs()  # 失败回滚控件到服务端真实状态
            return
        self.app.report(desc, result, error)
        self._load_configs()  # 以服务端状态为准回写控件（失败时即回滚）

"""主菜单屏：艺术字 logo（窄终端自动降级）+ 版本副标题 + 居中菜单 + 底部提示栏。"""
from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import OptionList, Static

from .. import __version__
from ..ui import LOGO_WIDTH, logo_text

try:  # textual 各版本 Option 导出位置不同
    from textual.widgets.option_list import Option
except ImportError:  # pragma: no cover
    from textual.widgets._option_list import Option


class HomeScreen(Screen):
    """功能主菜单：概览 / 配置管理 / 设置 / 切换端点。"""

    CSS_CLASSES = "logo-page"

    def __init__(self) -> None:
        super().__init__(classes="logo-page")

    BINDINGS = [
        Binding("escape,ctrl+c", "menu('quit')", "退出"),
        Binding("1", "menu('overview')", "概览", show=False),
        Binding("2", "menu('configuration')", "配置", show=False),
        Binding("3", "menu('settings')", "设置", show=False),
        Binding("4", "menu('switch')", "端点", show=False),
    ]

    ITEMS = [
        ("overview", "概览", "流量 / 内存 / 活动连接实时监控"),
        ("configuration", "配置管理", "配置提供方与拉取部署"),
        ("settings", "设置", "TUN / 模式 / 重载 / 升级 / 日志"),
        ("switch", "切换端点", "返回端点选择"),
    ]

    def compose(self):
        with Vertical(id="home-main"):
            with Vertical(id="home-col"):
                yield Static(logo_text(), id="home-banner")
                yield Static(Text("Clash Controller", style="bold"), id="home-plain")
                name = (self.app.profile or {}).get("name", "")
                yield Static(f"v{__version__}" + (f" · {name}" if name else ""), id="home-version")
                yield OptionList(id="home-list")
        yield Static("↑↓ 选择 · 回车 进入 · 1-4 直达 · Esc/Ctrl+C 退出", classes="page-hint")

    def on_mount(self) -> None:
        ol = self.query_one("#home-list", OptionList)
        ol.add_options(self._options())
        ol.highlighted = 0
        ol.focus()
        self._apply_breakpoint()

    def on_resize(self, event) -> None:
        self._apply_breakpoint()

    def _apply_breakpoint(self) -> None:
        """宽度不足以容纳艺术字时降级为普通标题文本。"""
        self.set_class(self.size.width < LOGO_WIDTH + 6, "-sm")

    def _options(self):
        for i, (_, label, desc) in enumerate(self.ITEMS, start=1):
            t = Text()
            t.append(f" {i}  ", style="bold")
            t.append(f"{label:<14}", style="bold")
            t.append(desc, style="dim")
            yield Option(t)

    def _open(self, key: str) -> None:
        from .config import ConfigScreen
        from .overview import OverviewScreen
        from .settings import SettingsScreen

        if key == "overview":
            self.app.push_screen(OverviewScreen())
        elif key == "configuration":
            self.app.push_screen(ConfigScreen())
        elif key == "settings":
            self.app.push_screen(SettingsScreen())
        elif key == "switch":
            self.app.clear_profile()
            self.app.add_log("Returning to endpoint selection.")
            self.app.pop_screen()
        elif key == "quit":
            self.app.exit()

    @on(OptionList.OptionSelected, "#home-list")
    def _on_select(self, event: OptionList.OptionSelected) -> None:
        self._open(self.ITEMS[event.option_index][0])

    def action_menu(self, key: str) -> None:
        self._open(key)

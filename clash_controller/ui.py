"""界面骨架组件：PageScreen（顶栏 / 滚动内容区 / 底部提示栏 / 折叠菜单）与首页艺术字。

本模块是纯展示层组件，不做任何网络 / IO；与 widgets.py（模态与表格辅助）共同构成 UI 基础库。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, OptionList, Static, TextArea

try:  # 不同 textual 版本 Option 导出位置不同
    from textual.widgets.option_list import Option
except ImportError:  # pragma: no cover
    from textual.widgets._option_list import Option

from .widgets import EDIT_HINT, STYLE_ERR

# ---------------------------------------------------------------- 首页 logo

LOGO_LINES = [
    " ██████╗██╗      █████╗ ███████╗██╗  ██╗ ██████╗████████╗██╗     ",
    "██╔════╝██║     ██╔══██╗██╔════╝██║  ██║██╔════╝╚══██╔══╝██║     ",
    "██║     ██║     ███████║███████╗███████║██║        ██║   ██║     ",
    "██║     ██║     ██╔══██║╚════██║██╔══██║██║        ██║   ██║     ",
    "╚██████╗███████╗██║  ██║███████║██║  ██║╚██████╗   ██║   ███████╗",
    " ╚═════╝╚══════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝   ╚═╝   ╚══════╝",
]

LOGO_WIDTH = max(len(line) for line in LOGO_LINES)


def logo_text() -> Text:
    return Text("\n".join(LOGO_LINES))


# ---------------------------------------------------------------- 页面骨架

MenuItem = tuple[str | Callable[[], str], Callable[[], None], bool]


class PageScreen(Screen):
    """功能页骨架：顶栏（返回 / 标题-副标题 / 右侧控件 / 折叠菜单）+ 内容区 + 底部提示栏。

    子类：设置 TITLE / HINT，重写 compose_page()（内容区）与可选 compose_toolbar()（顶栏右侧）。
    内容区版式：小容器（.panel：筛选 / 排序 / 主操作按钮）+ 大容器（表格 .tbl 1fr）。
    self.menu 仅登记次要 / 低频操作（Android ⋮ 思路），主操作应作为容器内按钮常驻；为空则不显示菜单按钮。
    菜单项元素为 (标签或取标签函数, 回调, 是否危险)。
    """

    TITLE: ClassVar[str] = ""
    SUBTITLE: ClassVar[str] = ""
    HINT: ClassVar[str] = "↑↓ 移动 · 回车 执行 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

    BINDINGS = [
        Binding("escape,ctrl+c", "page_back", "返回"),
        Binding("ctrl+r", "refresh_page", "刷新"),
        Binding("home", "page_top", "顶部", show=False),
        Binding("end", "page_bottom", "底部", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
    ]

    DEFAULT_CSS = """
    PageScreen {
        layers: base overlay;
        layout: vertical;
        overflow-y: hidden;
    }
    PageScreen #topbar {
        height: auto;
        padding: 0 1;
        background: $panel;
        layout: horizontal;
    }
    PageScreen #topbar Button {
        margin-right: 2;
    }
    PageScreen .tb-left {
        width: 1fr;
        height: auto;
        layout: horizontal;
    }
    PageScreen .tb-left Static {
        width: auto;
        content-align: left middle;
    }
    PageScreen .tb-title {
        text-style: bold;
    }
    PageScreen .tb-sub {
        margin-left: 2;
        color: $text-muted;
    }
    PageScreen .tb-right {
        width: auto;
        height: auto;
        layout: horizontal;
    }
    PageScreen .tb-right Select {
        margin-left: 2;
    }
    PageScreen #page {
        height: 1fr;
        padding: 0;
        layout: vertical;
        overflow-y: hidden;
    }
    PageScreen .page-hint {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
    }
    PageScreen #page-menu {
        display: none;
        position: absolute;
        overlay: screen;
        layer: overlay;
        width: 28;
        height: auto;
        border: round $primary;
        background: $surface;
    }
    PageScreen #menu-list {
        height: auto;
        border: none;
        background: transparent;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.menu: list[MenuItem] = []
        self._menu_open = False
        self._menu_prev_focus = None
        self.subtitle: str = self.SUBTITLE

    # ------------------------------------------------------------ 组合

    def compose(self):
        with Horizontal(id="topbar"):
            yield Button("◀ 返回", id="top-back", compact=True)
            with Horizontal(classes="tb-left"):
                yield Static(Text(self.TITLE, style="bold"), classes="tb-title")
                yield Static(self.subtitle, classes="tb-sub", id="tb-sub")
            with Horizontal(classes="tb-right"):
                yield from self.compose_toolbar()
                if self.menu:
                    yield Button("菜单 ▾", id="top-menu", compact=True)
        with Vertical(id="page"):
            yield from self.compose_page()
        yield Static(self.HINT, classes="page-hint", id="page-hint")
        if self.menu:
            with Vertical(id="page-menu"):
                yield OptionList(id="menu-list")

    def compose_page(self):
        return iter(())

    def compose_toolbar(self):
        return iter(())

    # ------------------------------------------------------------ 标题 / 副标题

    def set_subtitle(self, text: str) -> None:
        self.subtitle = text
        if self.is_mounted:
            self.query_one("#tb-sub", Static).update(text)

    def set_title(self, text: str) -> None:
        self.TITLE = text
        if self.is_mounted:
            self.query_one(".tb-title", Static).update(Text(text, style="bold"))

    # ------------------------------------------------------------ 折叠菜单

    def _menu_options(self) -> list[Option]:
        opts = []
        for label, _, danger in self.menu:
            text = label() if callable(label) else label
            item = Text(text)
            if danger:
                item.stylize(STYLE_ERR)
            opts.append(Option(item, id=str(len(opts))))
        return opts

    def _open_menu(self) -> None:
        panel = self.query_one("#page-menu")
        if self._menu_open:
            self._close_menu()
            return
        self._menu_open = True
        self._menu_prev_focus = self.app.focused
        panel.display = True
        ol = self.query_one("#menu-list", OptionList)
        ol.clear_options()
        ol.add_options(self._menu_options())
        ol.highlighted = 0
        btn = self.query_one("#top-menu", Button)
        region = btn.region
        width = panel.region.width or 28
        panel.styles.offset = (max(0, self.size.width - width - 1), region.bottom)
        ol.focus()

    def _close_menu(self) -> None:
        if not self._menu_open:
            return
        self._menu_open = False
        self.query_one("#page-menu").display = False
        prev = self._menu_prev_focus
        if prev is not None and prev.can_focus and prev.is_mounted:
            prev.focus()
        self._menu_prev_focus = None

    @on(Button.Pressed, "#top-menu")
    def _on_menu_button(self) -> None:
        self._open_menu()

    @on(OptionList.OptionSelected, "#menu-list")
    def _on_menu_pick(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        self._close_menu()
        if 0 <= idx < len(self.menu):
            self.menu[idx][1]()

    # ------------------------------------------------------------ 动作

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "top-back":
            self.action_page_back()

    def action_page_back(self) -> None:
        """Esc / ◀ 返回：浮层 > 返回。根屏子类覆写为退出。"""
        if self._menu_open:
            self._close_menu()
            return
        self.app.pop_screen()

    def on_click(self, event) -> None:
        """点击浮层外部时收起折叠菜单。"""
        if self._menu_open:
            panel = self.query_one("#page-menu")
            anchor = self.query_one("#top-menu")
            if not panel.region.contains(event.screen_x, event.screen_y) and not anchor.region.contains(event.screen_x, event.screen_y):
                self._close_menu()

    def action_refresh_page(self) -> None:
        self.reload_page()

    def reload_page(self) -> None:
        """子类覆写：重新取数。"""

    # ------------------------------------------------------------ 文本编辑模式

    def on_descendant_focus(self, event) -> None:
        self._refresh_hint()

    def on_descendant_blur(self, event) -> None:
        self._refresh_hint()

    def _editing(self) -> bool:
        return isinstance(self.app.focused, (Input, TextArea))

    def _refresh_hint(self) -> None:
        """焦点进出输入框时，在页面 HINT 与编辑键位 HINT 间切换。"""
        if not self.is_mounted:
            return
        try:
            hint = self.query_one("#page-hint", Static)
        except Exception:  # noqa: BLE001 — 组合早期尚未挂载
            return
        hint.update(EDIT_HINT if self._editing() else self.HINT)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """文本编辑模式下禁用页面刷新（Ctrl+R），其余交给控件原生键位。"""
        if action == "refresh_page" and self._editing():
            return False
        return True

    def _scroller(self):
        """Home/End/PgUp/PgDn 目标：优先内容区内的显式滚动容器（表格滚动走其自身键位）。"""
        page = self.query_one("#page", Vertical)
        inner = page.query(VerticalScroll)
        return inner.first() if inner else page

    def action_page_top(self) -> None:
        self._scroller().action_scroll_home()

    def action_page_bottom(self) -> None:
        self._scroller().action_scroll_end()

    def action_page_up(self) -> None:
        self._scroller().action_page_up()

    def action_page_down(self) -> None:
        self._scroller().action_page_down()

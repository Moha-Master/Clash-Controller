"""共享 UI 组件：确认、输入、多字段表单、输出查看、表格与模糊筛选辅助。

所有模态均为 ModalScreen 泛型返回值，配合 push_screen_wait / 回调消费；
数据展示统一使用 DataTable（ClickTable 单击执行），不构造字符串伪表格。
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, OptionList, Select, Static, Switch, TextArea

try:  # textual 各版本 Option 导出位置不同
    from textual.widgets.option_list import Option
except ImportError:  # pragma: no cover
    from textual.widgets._option_list import Option

# ---------------------------------------------------------------- 文案与配色

STYLE_OK = "#4ec96a"
STYLE_ERR = "#e06c75"
STYLE_WARN = "#e5c07b"
STYLE_INFO = "#61afef"
STYLE_DIM = "dim"


def tint(value: Any, style: str) -> Text:
    t = Text(str(value))
    t.stylize(style)
    return t


def colored_text(value: str, *, err: bool = False, warn: bool = False, dim: bool = False) -> Text:
    t = Text(value)
    if err:
        t.stylize(STYLE_ERR)
    elif warn:
        t.stylize(STYLE_WARN)
    elif dim:
        t.stylize(STYLE_DIM)
    return t


HINT_PICK = "输入筛选 · ↑↓ 移动 · 回车 确认 · Esc 取消"
HINT_MENU = "↑↓ 移动 · 回车 确认 · Ctrl+Q 退出"
# 文本编辑模式（焦点在 Input / TextArea）：GUI 风编辑键位，输入框聚焦时替换页面 HINT
HINT_FORM = "Tab 轮切字段 · Enter 提交 · Esc/Ctrl+C 取消"
EDIT_HINT = "Home/End 行首/行尾 · Ctrl+Shift+A 全选 · Ctrl+X/C/V 剪切/复制/粘贴 · Esc/Ctrl+C 返回"
HINT_FORM_EDIT = "Home/End 行首/行尾 · Ctrl+Shift+A 全选 · Ctrl+X/C/V 剪切/复制/粘贴 · Enter 提交 · Esc/Ctrl+C 取消"
HINT_FORM_TA = "Home/End 行首/行尾 · Ctrl+Shift+A 全选 · Ctrl+X/C/V 剪切/复制/粘贴 · Ctrl+Z/Y 撤销/重做 · Esc/Ctrl+C 取消"


def _truthy(value: Any) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- 模糊匹配

def fuzzy_score(needle: str, haystack: str):
    """返回可排序匹配分数（越小越优），不匹配返回 None。"""
    if not needle:
        return (0, 0, 0)
    n, h = needle.lower(), haystack.lower()
    pos = h.find(n)
    if pos >= 0:
        return (0, pos, 0)
    idx = -1
    first = -1
    for ch in n:
        idx = h.find(ch, idx + 1)
        if idx < 0:
            return None
        if first < 0:
            first = idx
    return (1, first, idx - first)


def filter_fuzzy(items: list, needle: str, key: Callable[[Any], str]) -> list:
    """按模糊分数排序过滤；needle 为空时原样返回。"""
    if not needle:
        return list(items)
    scored = []
    for i, it in enumerate(items):
        s = fuzzy_score(needle, key(it))
        if s is not None:
            scored.append((s, i, it))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [it for _, _, it in scored]


# ---------------------------------------------------------------- 确认弹窗

class ConfirmModal(ModalScreen[bool]):
    """通用确认框：三段式（标题栏 / 内容 / 按钮栏）。回车 = 确认，Esc / 取消 = 否。"""

    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal > .modal-box { width: 76; }
    ConfirmModal .cf-msg { height: auto; }
    """

    BINDINGS = [Binding("escape,ctrl+c", "cancel", show=False)]

    def __init__(self, message, title="请确认", *, yes="确认", no="取消", default_yes=True):
        super().__init__()
        self.message = message if isinstance(message, Text) else Text(str(message))
        self.title_text = title
        self.yes_label = yes
        self.no_label = no
        self.default_yes = default_yes

    def compose(self):
        with Vertical(classes="modal-box"):
            yield Static(Text(self.title_text, style="bold"), classes="modal-title")
            with Vertical(classes="modal-main"):
                yield Static(self.message, classes="cf-msg")
            with Horizontal(classes="btn-row"):
                yield Button(self.no_label, id="no", variant="default")
                yield Button(self.yes_label, id="yes", variant="primary" if self.default_yes else "error")

    def on_key(self, event) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------- 输入弹窗

class InputModal(ModalScreen[str | None]):
    """单行输入，三段式。validator 返回 False 时提示错误并停留。None=取消。"""

    DEFAULT_CSS = """
    InputModal { align: center middle; }
    InputModal > .modal-box { width: 80; }
    InputModal .im-error { color: $error; height: auto; }
    InputModal .im-hint { color: $text-muted; height: auto; }
    InputModal .im-msg { height: auto; margin-bottom: 1; }
    InputModal .im-input { margin-bottom: 1; }
    """

    BINDINGS = [Binding("escape,ctrl+c", "cancel", show=False)]

    def __init__(
        self,
        message,
        *,
        title="输入",
        value: str = "",
        validator: Callable[[str], bool] | None = None,
        error: str = "输入不合法",
        hint: str = "",
    ):
        super().__init__()
        self.message = message if isinstance(message, Text) else Text(str(message))
        self.title_text = title
        self.initial = value
        self.validator = validator
        self.error_text = error
        self.hint_text = hint

    def compose(self):
        with Vertical(classes="modal-box"):
            yield Static(Text(self.title_text, style="bold"), classes="modal-title")
            with Vertical(classes="modal-main"):
                yield Static(self.message, classes="im-msg")
                yield Input(value=self.initial, id="im-input", classes="im-input")
                if self.hint_text:
                    yield Static(self.hint_text, classes="im-hint")
                yield Static("", classes="im-error", id="im-error")
                yield HintBar("", classes="page-hint", id="im-hint")
            with Horizontal(classes="btn-row"):
                yield Button("取消", id="im-cancel", variant="default")
                yield Button("确定", id="im-ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#im-input", Input).focus()
        self._refresh_hint()

    def on_descendant_focus(self, event) -> None:
        self._refresh_hint()

    def on_descendant_blur(self, event) -> None:
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        if not self.is_mounted:
            return
        editing = isinstance(self.app.focused, (Input, TextArea))
        self.query_one("#im-hint", HintBar).update(HINT_FORM_EDIT if editing else HINT_FORM)

    def _submit(self) -> None:
        val = self.query_one("#im-input", Input).value.strip()
        if self.validator and not self.validator(val):
            self.query_one("#im-error", Static).update(Text(self.error_text, style=STYLE_ERR))
            return
        self.dismiss(val)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "im-ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------- 表单弹窗

@dataclass
class FormField:
    name: str
    label: str
    kind: str = "text"  # text | password | choice | switch | textarea | note
    value: str = ""  # switch: "on" 表示开启
    options: list = field(default_factory=list)  # choice: list[(label, value)]
    validator: Callable[[str], bool] | None = None
    error: str = "输入不合法"
    placeholder: str = ""
    hint: str = ""


class FormModal(ModalScreen["dict | None"]):
    """多字段表单，三段式，返回 {name: value}；None=取消。password 留空保持原值。"""

    DEFAULT_CSS = """
    FormModal { align: center middle; }
    FormModal > .modal-box { width: 92; min-height: 24; }
    FormModal .frm-note { color: $text-muted; height: auto; }
    FormModal .frm-row { height: auto; margin-bottom: 1; }
    FormModal .frm-label { width: 16; height: 1; content-align: right middle; text-style: bold; margin-right: 1; }
    FormModal .frm-field { width: 1fr; height: auto; }
    FormModal .frm-field Input, FormModal .frm-field Select { width: 1fr; margin-top: 0; }
    FormModal .frm-field TextArea { width: 1fr; height: 7; }
    FormModal .frm-field .frm-note { margin-top: 0; }
    FormModal .frm-field Switch { height: 1; border: none; padding: 0 1; }
    FormModal .frm-field Switch:focus { border: none; background-tint: $foreground 8%; }
    FormModal .frm-err { color: $error; height: 1; }
    FormModal .frm-body { height: 1fr; padding: 1 2; }
    FormModal .frm-pad { height: 1; }
    """

    BINDINGS = [Binding("escape,ctrl+c", "cancel", show=False)]

    def __init__(self, title: str, fields: list, *, message: str = "", ok_label="保存", extra_buttons=None):
        super().__init__()
        self.title_text = title
        self.fields = list(fields)
        self.message = message
        self.ok_label = ok_label
        # 额外按钮：list[(id, label, variant)]，点击后以按钮 id 作为结果 dismiss
        self.extra_buttons = list(extra_buttons or [])

    def compose(self):
        with Vertical(classes="modal-box"):
            yield Static(Text(self.title_text, style="bold"), classes="modal-title")
            with VerticalScroll(classes="frm-body"):
                if self.message:
                    yield Static(self.message, classes="frm-note")
                    yield Static("", classes="frm-pad")
                for f in self.fields:
                    if f.kind == "note":
                        yield Static(f.label, classes="frm-note")
                        continue
                    with Horizontal(classes="frm-row"):
                        yield Label(f.label, classes="frm-label")
                        with Vertical(classes="frm-field"):
                            if f.kind in ("text", "password"):
                                yield Input(
                                    value=f.value if f.kind == "text" else "",
                                    password=(f.kind == "password"),
                                    placeholder=(mask_secret(f.value) if f.kind == "password" else f.placeholder),
                                    compact=True,
                                    id=f"in-{f.name}",
                                )
                            elif f.kind == "choice":
                                yield Select(
                                    [(lab, val) for lab, val in f.options],
                                    value=f.value if f.value else Select.NULL,
                                    allow_blank=not f.value,
                                    compact=True,
                                    id=f"se-{f.name}",
                                )
                            elif f.kind == "switch":
                                yield Switch(
                                    value=_truthy(f.value),
                                    id=f"sw-{f.name}",
                                )
                            elif f.kind == "textarea":
                                yield TextArea(f.value, id=f"ta-{f.name}")
                            if f.hint:
                                yield Static(f.hint, classes="frm-note")
            yield Static("", classes="frm-err", id="frm-error")
            yield HintBar("", classes="page-hint", id="frm-hint")
            with Horizontal(classes="btn-row"):
                yield Button("取消", id="frm-cancel", variant="default")
                yield Button(self.ok_label, id="frm-ok", variant="primary")
                for bid, label, variant in self.extra_buttons:
                    yield Button(label, id=bid, variant=variant)

    def on_mount(self) -> None:
        for widget_type in (Input, Select, Switch, TextArea):
            found = self.query(widget_type)
            if found:
                found.first().focus()
                break
        self._refresh_hint()

    def on_descendant_focus(self, event) -> None:
        self._refresh_hint()

    def on_descendant_blur(self, event) -> None:
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        if not self.is_mounted:
            return
        focused = self.app.focused
        if isinstance(focused, TextArea):
            text = HINT_FORM_TA
        elif isinstance(focused, Input):
            text = HINT_FORM_EDIT
        else:
            text = HINT_FORM
        self.query_one("#frm-hint", HintBar).update(text)

    def _collect(self) -> "dict | None":
        """收集表单值；校验失败时写入错误信息并返回 None。可在子类扩展。"""
        err_box = self.query_one("#frm-error", Static)
        err_box.update(Text(""))
        out = {}
        for f in self.fields:
            if f.kind == "note":
                continue
            if f.kind == "choice":
                val = self.query_one(f"#se-{f.name}", Select).value
                val = "" if val is Select.NULL else str(val)
            elif f.kind == "switch":
                val = bool(self.query_one(f"#sw-{f.name}", Switch).value)
            elif f.kind == "textarea":
                val = self.query_one(f"#ta-{f.name}", TextArea).text.strip()
            else:
                val = self.query_one(f"#in-{f.name}", Input).value
                if f.kind == "password":
                    if not val and f.value:
                        val = f.value  # 密码留空保持原值
                else:
                    val = val.strip()
            if f.validator and not f.validator(val):
                err_box.update(Text(f"{f.label}: {f.error}", style=STYLE_ERR))
                return None
            out[f.name] = val
        return out

    def show_error(self, message: str) -> None:
        self.query_one("#frm-error", Static).update(Text(message, style=STYLE_ERR))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        data = self._collect()
        if data is not None:
            self.dismiss(data)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "frm-ok":
            data = self._collect()
            if data is not None:
                self.dismiss(data)
        elif bid == "frm-cancel":
            self.dismiss(None)
        else:
            # 额外按钮：以按钮 id 作为结果返回，由调用方解释
            self.dismiss(bid)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------- 输出查看弹窗

class OutputModal(ModalScreen[None]):
    """滚动文本查看弹窗（长文本/详情预览等），三段式，Esc / 回车 / 关闭按钮退出。"""

    DEFAULT_CSS = """
    OutputModal { align: center middle; }
    OutputModal > .modal-box { width: 100; height: 90%; }
    OutputModal .out-body { height: 1fr; border: none; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape,ctrl+c", "close", show=False),
        Binding("enter", "close", show=False),
    ]

    def __init__(self, title: str, content):
        super().__init__()
        self.title_text = title
        self.content = content if isinstance(content, Text) else Text(str(content))

    def compose(self):
        with Vertical(classes="modal-box"):
            yield Static(Text(self.title_text, style="bold"), classes="modal-title")
            with VerticalScroll(classes="out-body"):
                yield Static(self.content)
            with Horizontal(classes="btn-row"):
                yield Button("关闭", id="out-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------- 表格辅助

class HintBar(ScrollableContainer):
    """单行操作提示栏：内容宽于容器时，像 LED 屏一样来回滚动（端点停留约 3s）。"""

    DEFAULT_CSS = """
    HintBar {
        height: 1;
        overflow-x: auto;
        overflow-y: hidden;
        scrollbar-size: 0 0;
    }
    HintBar .hint-text {
        width: auto;
        height: 1;
    }
    """

    TICK = 0.25
    HOLD = 12  # 到达端点后停留的拍数（12 x 0.25s = 3s）

    def __init__(self, text: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._text = text
        self._direction = 1
        self._hold = 0

    def compose(self):
        yield Static(Text(self._text, no_wrap=True), classes="hint-text")

    def on_mount(self) -> None:
        self.set_interval(self.TICK, self._tick)

    def update(self, text: str) -> None:
        self._text = text
        self._direction = 1
        self._hold = 0
        if self.is_mounted:
            self.query_one(".hint-text", Static).update(Text(text, no_wrap=True))
            self.scroll_x = 0

    def _tick(self) -> None:
        total = self.virtual_size.width
        view = self.size.width
        if total <= view:
            if self.scroll_x:
                self.scroll_x = 0
            return
        if self._hold > 0:
            self._hold -= 1
            return
        end = total - view
        x = int(self.scroll_x) + self._direction
        if x >= end:
            x = end
            self._direction = -1
            self._hold = self.HOLD
        elif x <= 0:
            x = 0
            self._direction = 1
            self._hold = self.HOLD
        self.scroll_x = x


def marquee_window(text: str, width: int, offset: int) -> str:
    """在 width 单元格内取 text 的循环窗口，起点为第 offset 个字符（跑马灯用）。"""
    if width <= 0 or not text:
        return " " * max(0, width)
    s = text + "   "
    n = len(s)
    used = 0
    out: list[str] = []
    i = offset % n
    while used < width and len(out) <= n + width:
        ch = s[i % n]
        w = cell_len(ch) or 1
        if used + w > width:
            break
        out.append(ch)
        used += w
        i += 1
    return "".join(out) + " " * (width - used)


class MenuMarquee:
    """首页 OptionList 菜单行控制器：描述右对齐，超宽时来回跑马灯。

    屏幕 on_mount 用法::

        self._marquee = MenuMarquee(self, "home-list", [(label, desc), ...])
        ol.add_options([Option("") for _ in self._marquee.rows])
        self.call_after_refresh(self._marquee.start)
    """

    TICK = 0.25
    HOLD = 12

    def __init__(self, screen, list_id: str, rows, *, label_width: int = 14) -> None:
        self.screen = screen
        self.list = screen.query_one(f"#{list_id}", OptionList)
        self.rows = list(rows)
        self.label_width = label_width
        self.offset = 0
        self.direction = 1
        self.hold = 0
        self._timer = None

    def placeholder_options(self):
        """与 rows 等长的占位 Option（先 add_options，再由 sync 填充文本）。"""
        return [Option("") for _ in self.rows]

    def start(self) -> None:
        self.sync()
        if self._timer is None:
            self._timer = self.screen.set_interval(self.TICK, self._tick)

    def set_rows(self, rows) -> None:
        self.rows = list(rows)
        self.offset = 0
        self.direction = 1
        self.hold = 0
        self.sync()

    def sync(self) -> None:
        node = self.list
        if not node.is_mounted or node.option_count != len(self.rows):
            return
        for idx in range(len(self.rows)):
            node.replace_option_prompt_at_index(idx, self._prompt(idx))

    def _room(self) -> int:
        node = self.list
        width = node.content_size.width
        if node.show_vertical_scrollbar:
            width -= node.scrollbar_size_vertical
        return max(6, width - self.label_width - 4)

    def _prompt(self, idx: int) -> Text:
        label, desc = self.rows[idx]
        label = shorten(label, self.label_width)
        pad = " " * max(0, self.label_width - cell_len(label))
        prefix = f" {idx + 1:<2} {label}{pad}"
        room = self._room()
        if cell_len(desc) <= room:
            window = " " * (room - cell_len(desc)) + desc
        else:
            window = marquee_window(desc, room, self.offset)
        text = Text(no_wrap=True, overflow="crop")
        text.append(prefix, style="bold")
        text.append(window, style="dim")
        return text

    def _tick(self) -> None:
        room = self._room()
        longest = max((cell_len(d) for _, d in self.rows), default=0)
        if longest <= room:
            if self.offset:
                self.offset = 0
                self.sync()
            return
        if self.hold > 0:
            self.hold -= 1
            return
        span = max(cell_len(d) + 3 for _, d in self.rows if cell_len(d) > room)
        self.offset += self.direction
        if self.offset >= span:
            self.offset = span - 1
            self.direction = -1
            self.hold = self.HOLD
        elif self.offset <= 0:
            self.offset = 0
            self.direction = 1
            self.hold = self.HOLD
        self.sync()


class ClickTable(DataTable):
    """列表即操作的表格：单击数据单元格 = 移动光标并立即发 RowSelected。

    注意：Textual 沿 MRO 逐类派发事件，重写 `_on_click` 后父类实现仍会被调用一次，
    因此接管分支与回落分支都必须 prevent_default()，防止 RowSelected 双发。
    """

    async def _on_click(self, event: events.Click) -> None:
        meta = event.style.meta if event.style is not None else {}
        row = meta.get("row", -1)
        col = meta.get("column", -1)
        is_data_cell = (
            self.cursor_type == "row"
            and isinstance(row, int) and isinstance(col, int)
            and 0 <= row < self.row_count and col >= 0
            and not meta.get("out_of_bounds", False)
        )
        if is_data_cell:
            self.cursor_coordinate = Coordinate(row, col)
            self._post_selected_message()
        else:
            await super()._on_click(event)
        event.prevent_default()


def make_table(*headers, cursor: str = "row", zebra: bool = True, classes: str = "tbl", **kwargs) -> ClickTable:
    dt = ClickTable(cursor_type=cursor, zebra_stripes=zebra, classes=classes, **kwargs)
    dt.add_columns(*headers)
    return dt


def load_rows(table: DataTable, rows: list) -> DataTable:
    """全量重填表格。每行是可被 add_row 展开的单元格序列（str 或 Text）。"""
    table.clear()
    for row in rows:
        table.add_row(*row)
    return table


def shorten(text: str, limit: int) -> str:
    """按显示宽度截断，超出部分以省略号结尾。"""
    text = str(text)
    if cell_len(text) <= limit:
        return text
    out = ""
    for ch in text:
        if cell_len(out + ch) > limit - 1:
            break
        out += ch
    return out + "…"


def mask_secret(v: str | None, keep: int = 4) -> str:
    """密钥/口令脱敏展示：首尾各 keep 位，过短则全圆点（placeholder 专用）。"""
    s = (v or "").strip()
    if not s:
        return ""
    if len(s) <= keep * 2 + 1:
        return "•" * min(8, max(6, len(s)))
    return f"{s[:keep]}…{s[-keep:]}"


def rcell(text, content_width: int) -> Text:
    """右对齐单元格：文本在 content_width 内右对齐。"""
    pad = max(0, content_width - cell_len(str(text)))
    t = Text(" " * pad + str(text))
    return t


def fit_table_columns(table: DataTable, weights: list[float], rows: int | None = None) -> list[int]:
    """按权重把表格可视内容宽（含每列左右各 1 空格 padding）瓜分给各列。

    返回每列的可视宽度（含 padding），cell 的内容宽 = 可视宽 - 2。
    需垂直滚动条时额外预留 2 列。传入 rows（即将装载的行数）可**确定性**预判滚动条，
    避免依赖会变化的 max_scroll_y 导致首帧与二次布局算出不同列宽、右对齐列错位。
    """
    ncols = len(weights)
    if rows is not None:
        capacity = (table.region.height or 0) - 3  # 上下边框 + 表头
        need_vbar = capacity <= 0 or rows > capacity
    else:
        need_vbar = table.max_scroll_y > 0
    sbv = 2 if need_vbar else 0
    avail = max(ncols * 4, (table.region.width or ncols * 20) - 2 - sbv)
    total_w = sum(weights) or ncols
    visible = [max(4, int(avail * w / total_w)) for w in weights]
    # 舍入余量补给最宽列，保证恰好铺满
    visible[visible.index(max(visible))] += avail - sum(visible)
    for (_, column), vis in zip(table.columns.items(), visible, strict=True):
        column.width = vis - 2  # Column.width 不含列内左右 padding
        column.auto_width = False
    table.refresh(layout=True)
    return visible


__all__ = [
    "ConfirmModal", "InputModal", "FormField", "FormModal", "OutputModal",
    "ClickTable", "make_table", "load_rows", "filter_fuzzy", "fuzzy_score",
    "shorten", "rcell", "fit_table_columns",
    "tint", "colored_text",
    "STYLE_OK", "STYLE_ERR", "STYLE_WARN", "STYLE_INFO", "STYLE_DIM",
    "HINT_PICK", "HINT_MENU", "HINT_FORM", "EDIT_HINT", "HINT_FORM_EDIT", "HINT_FORM_TA",
]

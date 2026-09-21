# Textual 开发参考 —— aliyun-controller 界面打磨

> 权威依据：本地已安装源码 `venv/lib/python3.14/site-packages/textual/`，版本 **Textual 8.2.8**（`textual.__version__` 确认）。
> 本文所有类名 / 方法 / CSS 属性 / 键名均在本地源码 grep 验证，或以 `App.run_test` 内存运行实测；
> 与官方在线文档（textual.textualize.io）冲突时以本地 8.2.8 为准，并已标注差异。
> 在线文档结构有变：**旧的 `guide/keys/` 已并入 `guide/input/`**；CSS 指南大写为 `/guide/CSS/`。

## 0. 速查表（各主题结论）

| 主题 | 8.2.8 是否支持 | 一句话结论 |
| :- | :- | :- |
| 三段式布局 | 支持 | `dock` + `height: 1fr` 即可，项目现状已正确 |
| 双滚动条 | 需修复 | 表格 `height: 1fr` 且只留一个 1fr 子项；8.2.8 表格默认 `auto`+`max-height:100%` 已天然收敛 |
| 滚轮作用于光标下方容器 | 原生支持 | 命中测试→沿祖先冒泡，内层到底外层接手（实测）；无需自定义 |
| resize 自适应 | 支持 | `on_resize`（任意 Widget/Screen/App）+ 内置 `HORIZONTAL_BREAKPOINTS` 断点类（更优） |
| 无依赖 ASCII banner | 无现成件 | pyfiglet/Rich/Textual 均无 figlet；用自带小型 block 字体字典，降级为加粗文本；pyfiglet 仅作可选依赖 |
| ModalScreen 三段式 | 支持 | 项目现有模式即最佳实践；自动 dim 背景 |
| 页面内非全屏浮层 | 支持 | `position: absolute` + `offset` + `layer` + `overlay: screen` + `display` 切换（实测可命中鼠标事件） |
| 双色进度条 | 可行 | 子类化 `ProgressBar.BAR_RENDERABLE`（实测生效），或两个百分比宽度 Container 自绘 |
| 月份选择器 | 多种 | 推荐「左右箭头 + 文本」复合组件；Select 带 type-to-search 也够用 |
| 键名 ctrl+r 等 | 全部有效 | 逐一在 `textual/keys.py` 验证（见 §5.1 表） |
| Home/End 焦点区分 | 可行 | 焦点在 Input 时由 Input 自身绑定消费（实测）；打印键由 `check_consume_key` 自动放行 |
| DataTable 双击进入 | 原生 | 单击仅移光标，同一格再击 / 双击发 `RowSelected`（实测） |
| `.instant` 类 | **不存在** | 本地全库 grep 无此机制；需要"立即"用 `App.animation_level = "none"` |
| 主题统一按钮语义色 | 支持 | `App.theme` reactive + 21 个内置主题 + `register_theme(Theme(...))` |
| Spinner | **无 Spinner** | 8.2.8 用 `LoadingIndicator` / `Widget.loading` |

---

## 1. 布局与滚动

### 1.1 页面三段式：顶栏固定 / 内容可滚 / 底栏置底

**需求**：`标题/工具栏固定在顶部`、`内容区占满剩余高度`、`操作提示条始终贴底`。

**支持性**：完整支持。`Screen.DEFAULT_CSS` 为 `layout: vertical; overflow-y: auto;`（`screen.py:174`）——**Screen 本身就是垂直布局且默认可滚动**；`dock` 属性（`css/_style_properties.py::DockProperty`，取值 `top|bottom|left|right`，缺省 `"none"`）、`overflow`（合法值仅 `scroll|hidden|auto`，见 `css/constants.py::VALID_OVERFLOW`，**没有 `visible`**）、`height: 1fr` 均可用；`containers.py` 提供 `Vertical/Horizontal/VerticalScroll/HorizontalScroll/Center/CenterMiddle/Grid` 等。

**推荐做法**（与项目 `app.tcss #status` 一致，无需改动）：

```tcss
/* app.tcss —— App.compose: Header / 内容 / StatusBar / Footer */
Screen {
    layout: vertical;      /* 默认即 vertical，可省略 */
}

#page {
    height: 1fr;           /* 内容区吃掉剩余高度 */
    layout: vertical;
}

#status {                  /* 底栏：置底，不参与内容滚动 */
    height: 1;
    dock: bottom;
    background: $panel;
}
```

也可用 grid 显式分行（顶栏 1fr 内容、底部 auto 提示）：

```tcss
Page {
    grid-rows: auto 1fr auto;
}
```

**陷阱**：
- `dock` 是**占据空间**的贴边（类似 margin box 收缩），不是悬浮；同边多个 dock 按 compose 顺序依次排列。
- 底栏想要"永远最后"也可以不用 dock——把 `StatusBar` 直接放 `compose` 的 `Footer` 之前即可；项目当前 `Header → #status(dock bottom) → Footer` 的写法已验证有效。
- 额外可关注新属性 `split`（`SplitProperty`，取值同 dock 边）：`KeyPanel`/`HelpPanel` 就是靠 `split: right` 把面板从右侧"劈"出一块空间，适合侧栏需求。

### 1.2 DataTable 与外层滚动容器：双滚动条成因与修复

**需求**：表格外层若包了 `VerticalScroll`，希望只有表格内部滚动。

**支持性与实测**（8.2.8 行为）：
- 8.2.8 中 `DataTable.DEFAULT_CSS` 是 `height: auto; max-height: 100%;`（源码 `_data_table.py:331`）——表体自身封顶于容器 100%，内部滚动。
- `App.run_test` 实测：100 行表格放入 `VerticalScroll`：
  - 表格显式 `height: 1fr` → 外层 `virtual_size == viewport`（外层 `max_scroll_y=0`），滚轮滚的是表格内部 —— **无双滚动条**；
  - 表格用默认 `height: auto` → 外层仍多出 1 行可滚（表头/边距所致），出现外层 1 格 + 内层滚动条共存。

**成因**：外层容器的 `overflow-y: auto` + 子项总高（含 `margin`）> 视口 → 外层产生虚拟滚动；同时 DataTable 自身也是 `ScrollView`，两套滚动条并存，滚轮按"光标所在最内可滚者"分配，体验割裂。

**修复**（推荐顺序）：
1. **页面不要用滚动容器包裹表格**：`Screen`/`Vertical`（非 Scroll）+ 表格 `height: 1fr`。项目 `.tbl { height: 1fr; }` 已是正确模式；
2. 若页面确有其它长内容需要滚动（如账单信息面板），用独立 `VerticalScroll` 只包那部分（`height: 6 或 max-height`），表格留在滚动容器外；
3. 实在需要整体滚动预览时，把表格 `height` 设死（`1fr`），并给外层 `overflow-y: hidden`（内容被裁，不推荐）。

```python
def compose(self):
    yield Static(Text("解析记录", style="bold"), classes="page-title")
    with Horizontal(classes="toolbar"): ...
    yield make_table(...)          # .tbl: height:1fr —— 只有它内部滚动
    yield Static("↑↓ 移动 · 回车 操作", classes="page-hint")
```

**陷阱**：
- 注意"外层滚动容器"很可能就是 Screen 本身（默认 `overflow-y: auto`）：固定行（标题+工具栏+提示+margin）一旦总高超过视口，出现的其实是 Screen 的滚动条，与表格内部滚动条并存——所以固定行都用 `height: auto`、表格 `1fr` 吃掉剩余，才是根治；
- DataTable 在 1fr 下**滚轮与光标滚动都工作**（实测 `scroll_y` 变化发生在表格上）；但 `Ctrl+Home/End` 在表格绑定里是"跳到首/尾行"（`_data_table.py::BINDINGS`），不是页面滚动。
- `margin: 1` 会计入外层虚拟尺寸，是 auto 表格"差 1 行双滚动条"的常见来源。

### 1.3 鼠标滚轮派发规则（"鼠标在哪滚哪"）

**需求**：确认嵌套滚动容器时滚轮目标。

**支持性**：8.2.8 原生即"光标下方优先"。源码链路：
`App.on_event → Screen._forward_event → get_widget_at(x,y)`（命中测试）→ `widget._forward_event`；`Widget._on_mouse_scroll_up/down` 中若自身可滚则 `event.stop()`，否则不 stop → 事件（`MouseEvent` 系 `bubble=True`）**冒泡给最近的可滚动祖先**；滚到底/顶时 `_scroll_*_for_pointer` 返回 False，事件继续上抛（实测：内层到底后继续滚，外层 `scroll_y` 从 30 增到 32）。

**自定义**：
- 步长：`App.scroll_sensitivity_x / scroll_sensitivity_y`（普通实例属性，默认 4.0 / 2.0 格），可直接赋值调整；
- 想完全接管：给 widget 写 `async def _on_mouse_scroll_down(self, event: events.MouseScrollDown)` 并 `event.stop()`；或子类里 `on_event` 拦截；
- `ctrl` 或 `shift` + 滚轮 = 水平滚动（`_on_mouse_scroll_up` 源码分支）。

**陷阱**：
- 非滚动兄弟容器不会替别人滚：光标停在顶栏上滚动，下方内容区不动（实测 `scroll_y` 不变）——这是设计行为，提示用户"滚内容要放在内容上"。
- `widget.loading` 为 True 时该 widget 区域内鼠标事件被吞（`screen.py`：`if widget.loading: return`）。
- 被 `disabled` 的 widget 仍会接收滚轮事件（`_MOUSE_EVENTS_ALLOW_IF_DISABLED` 含四种 scroll 事件），但接收 mouse down/up 被禁止。

### 1.4 终端宽度自适应 / resize 监听

**需求**：宽度不足时，艺术字标题降级为普通文本。

**支持性**：两种方式均可：
1. `events.Resize`（`bubble=False`）：`App._on_resize` 内部处理后，布局阶段对每个尺寸变化的 widget 直接 post `Resize`（`screen.py:1342/1373`），因此**任意 Widget/Screen 定义 `on_resize(self, event)` 都会被调用**（实测 `pilot.resize_terminal(60,15)` 后收到 `Resize(size=(60,15))`）；事件携带 `event.size / event.virtual_size / event.container_size`。
2. **内置响应式断点类（8.x 特性，推荐）**：`App 或 Screen` 上设置 `HORIZONTAL_BREAKPOINTS = [(0,"-sm"), (80,"-md"), (120,"-lg")]`，`Screen._on_resize` 会自动给 Screen `update_classes`（源码 `screen.py:1509-1543`）。

```python
class HomeScreen(Screen):
    HORIZONTAL_BREAKPOINTS = [(0, "-sm"), (80, "-md")]
```

```tcss
HomeScreen .banner { display: block; }     /* 宽终端：ASCII 艺术字 */
HomeScreen .plain  { display: none; }
HomeScreen.-sm .banner { display: none; }  /* 窄终端：降级 */
HomeScreen.-sm .plain  { display: block; }
```

**陷阱**：
- `Resize` 在每次相关布局后可能连续触发（实测挂载即收到两次同尺寸事件），做昂贵计算要防抖：`self.call_later` 或 `@debounce`（`textual._callback`/`work` 装饰器），或监听 `self.screen.size` 的 watch 而非重复渲染。
- 断点类只打在 **Screen** 上，CSS 选择器须从 `HomeScreen.xxx` 或 `Screen.-sm` 起手。
- TCSS 没有 `@media`，不要照搬 web 文档。

---

## 2. 文本与样式

### 2.1 居中艺术字（ASCII banner）

**需求**：首页大标题用 figlet 风格艺术字，且不新增硬依赖。

**本地支持性**（逐项验证）：
- `pyfiglet`：**未安装**（`ModuleNotFoundError` 实测），且按约束本次不安装；
- Rich 15.0：无 figlet/banner 能力（`rich.text` 无相关符号）；
- Textual 8.2.8：无 banner 组件；最接近的是 `Digits`（`widgets/_digits.py`，3×3 点阵字符），但其字符集仅 `" 0123456789+-^x:ABCDEF$£€()"`（`renderables/digits.py:8`）——**没有小数点、百分号、G/g 等字母**，不能拼 "Aliyun Controller"，只适合纯数字金额/流量展示。

**推荐做法**：小型 block 字体自查找表 + 可选依赖降级。无依赖方案（20 行大写字母 A–Z 覆盖不了也没关系——只给标题需要的字配字形，缺失字母回退为大写单行块）：

```python
BLOCK5 = {  # 5 行 block 字体（示例截取，按需补全字母表）
    "A": (" ██ ", "█  █", "████", "█  █", "█  █"),
    "L": ("█   ", "█   ", "█   ", "█   ", "████"),
    "I": ("███", " █ ", " █ ", " █ ", "███"),
    "Y": ("█  █", "█  █", " ██ ", "  █ ", "  █ "),
    "N": ("█  █", "██ █", "████", "█ ██", "█  █"),
    "U": ("█  █", "█  █", "█  █", "█  █", " ██ "),
    " ": ("   ", "   ", "   ", "   ", "   "),
    # ...
}

def banner(text: str, gap: str = "  ") -> str | None:
    glyphs = []
    for ch in text.upper():
        if ch not in BLOCK5:
            return None          # 缺字形 → 整体降级
        glyphs.append(BLOCK5[ch])
    return "\n".join(gap.join(g[r] for g in glyphs) for r in range(5))

def render_title(text: str, width: int) -> str:
    art = banner(text)
    if art is None or max(len(line) for line in art.splitlines()) > width - 4:
        return text              # 无依赖降级：普通文本（外面用 bold 样式）
    return art
```

首页挂载时 `Static(banner(...), id="banner")` + §1.4 的断点类 `display` 切换即可。若允许可选依赖，可 `try: import pyfiglet / except ImportError: 回退上面函数`——注意 `pyproject.toml` 里放 `[project.optional-dependencies] art = ["pyfiglet"]`（本次仅调研结论，未安装）。

**陷阱**：
- 艺术字行宽按**终端单元格**算：`█` 宽度 1 没问题，但含 emoji/全角字符的 banner 会被 `wcwidth` 撑爆；
- `Static` 渲染多行字符串直接带 `\n` 即可，不要 `print`；
- 终端不支持真彩色时 `dim`/`bright` 风格可能丢失，考虑 `&:ansi` 伪类降级。

### 2.2 行内小标题 / 副标题右对齐

**需求**：标题行右侧挂计数、排序说明等小字。

**支持性**：`text-align`（合法值 `start|end|left|right|center|justify`，`css/constants.py::VALID_TEXT_ALIGN`）、`content-align-horizontal`/`align-horizontal`（`left|center|right`）、`align`（二维简写 `align: center middle`）、以及 **border 副标题**：`Widget.border_title / border_subtitle`（`widget.py:384`）配 `border-title-align` / `border-subtitle-align`（**subtitle 默认即 right**，`styles.py:346`）。

**推荐做法**（三种任选）：

```tcss
/* a) Horizontal 内 1fr 文本右对齐 */
.title-row { height: auto; padding: 0 1; }
.title-row .sub { width: 1fr; text-align: right; color: $text-muted; }
```

```python
with Horizontal(classes="title-row"):
    yield Static(Text("解析记录", style="bold"))
    yield Static(f"{n}/{m}", classes="sub")
```

```python
/* b) 边框副标题：一行搞定，无需额外 widget */
panel = Static(body, classes="panel")
panel.border_title = "账单归纳"
panel.border_subtitle = "2026-08 · 共 12 项"   # 默认右对齐
```

**陷阱**：
- `text-align` 只影响文本在**自身内容盒**内的排布；widget 宽度若是 `auto` 会收缩为文本宽度导致"看起来没对齐"——右对齐文本要给 `width: 1fr`；
- `align-horizontal`/`content-align` 对齐的是**子 widget**，不对齐文本，别混用；
- 项目 app.tcss 用的 `align-horizontal: right` 在 8.2.8 仍是合法处理器（`process_align_horizontal`，同时是 `content-align-horizontal` 的别名）。

### 2.3 表单字段行距 / 紧凑表单

**需求**：Label + Input/Select 的行内垂直间距过大。

**支持性**：
- `Input` 默认 `padding: 0 2; height: 3; border: tall ...`（`_input.py::DEFAULT_CSS`）；
- **8.2.8 起 `Input/Select/OptionList/Button/RadioSet/ToggleButton/Switch` 均有 `compact` reactive（构造参数 `compact=True`）**，切换 `-textual-compact` 类：去边框、`height: 1`、`padding: 0`（源码各 DEFAULT_CSS 的 `&.-textual-compact` 块）——这是官方紧凑通道；
- 盒模型 `padding/margin/border` 全部可用，注意 `border` 占 1 行/列。

**紧凑表单 CSS 模式**：

```tcss
CompactForm .frm-row { height: auto; margin-bottom: 0; }
CompactForm .frm-label { width: 18; padding: 0 1 0 0; text-align: right; }
CompactForm Input { height: 1; padding: 0 1; margin: 0; }
CompactForm Select { height: 1; margin: 0; }
```

```python
yield Input(value=..., compact=True)          # 或整表加 .CompactForm 类
yield Select(options, compact=True)
```

**陷阱**：
- 文本行距：`line-height`（widget 级）与 `text-style` 控制字体效果，`padding` 不要同时给 0 又指望 `line-spacing`——TCSS 没有 line-height 简写外的字段间距；
- `Input` 高度设 1 后 focus 边框消失（border 吃 2 行），compact 模式自带 `border: none !important`，自定义时记得同时清 `border-top/bottom`；
- 校验错误行用固定 `height: 1` 的 Static 占位（项目 FormModal `.frm-err` 已是此法），避免出现/消失导致跳版。

---

## 3. 组件

### 3.1 Button

**支持性**：`_VALID_BUTTON_VARIANTS = {"default","primary","success","warning","error"}`（`_button.py:32`，**无 link variant**，旧文档里的 `link` 不在本地版）。语义色来自主题的 `$primary/$success/$warning/$error`。`Button.Pressed` 在 Click 处理中发出；`enter` 键绑到 `press`（`BINDINGS`，space 不绑定）；`active_effect_duration`（默认 0.2s 按压动画）可调；构造参数含 `action=`（直接绑 action，不发 Pressed）。

**语义约定建议**（与 widgets.py 现状一致）：
- `default`：一般动作（取消/返回）；`primary`：推荐动作（确认/保存）；`success`：完成类；`warning`：需注意；`error`：危险（删除）。
- 摆放：对话框底栏右对齐（项目 `.btn-row` 已实现）；危险按钮放最右（项目把"删除"作为 extra button 排在"取消"左侧，建议改为最右）；主操作靠左于取消还是靠右，团队内保持一致即可，惯例是 `[次要] [取消] [主操作]` 或 `[主操作] [取消]`。
- 单击即触发 `Pressed`（无双击确认）；危险确认必须走 `ConfirmModal`，不要依赖 widget 自身。

```python
yield Button("删除", id="del", variant="error")
with Horizontal(classes="btn-row"):           # align-horizontal: right
    yield Button("取消", id="cancel")          # 一般动作
    yield Button("保存", id="ok", variant="primary")  # 推荐动作（可被 Enter 直达）
```

**陷阱**：
- 给普通文本加 `background` 会让其看起来像按钮——正文强调用 `color: $accent` + `text-style: bold`，背景色只留给真正的交互控件；
- 按钮上的 `[bold]` markup：label 是 RenderableType，`Text("…", style="bold")` 直接可用；
- `focus()` 后 Enter 即 press；模态里若同时 `on_key` 处理 Enter（项目 ConfirmModal 有），注意两者都会触发 `dismiss`，已用 `event.stop()+prevent_default()` 防御即可。

### 3.2 ProgressBar 双色/分段显示（20G 内一色、超额另一色）

**需求**：流量条在阈值前后颜色不同。

**支持性**：
- `ProgressBar` 本体**不内置阈值分段**；组件类 `bar--bar / bar--complete / bar--indeterminate` 只是整条/满条两态；
- `gradient` reactive：`from textual.color import Gradient`，**8.2.8 构造函数是 stops 形式**：`Gradient((0.0, "green"), (0.2, "yellow"), (1.0, "red"), quality=50)`（`color.py:682`；旧版 `Gradient(colors=[...])` 签名已不存在）。渐变沿整条 bar 铺，无法"20G 以内纯色、超出部分纯色"；
- **可行钩子**：`ProgressBar.BAR_RENDERABLE` 类变量 + 内部 `Bar` 子 widget 的 `bar_renderable` 参数（`_progress_bar.py:74/227`）。`Bar.render()` 用 `highlight_range=(0, width*percentage)` 调用它——换成自定义 renderable 即可任意分色（**实测 `MyPB.BAR_RENDERABLE = TwoTone` 后渲染管线正常产出自定义对象**）。

**方案 A（推荐）：自定义 BAR_RENDERABLE**：

```python
from rich.console import ConsoleOptions, RenderResult
from rich.text import Text
from textual.widgets import ProgressBar

class TwoToneBar:
    """highlight_range 内先画安全额度色，超过阈值部分画告警色。"""
    def __init__(self, highlight_range=(0, 0), highlight_style="green",
                 background_style="grey37", clickable_ranges=None,
                 width=None, gradient=None):
        self.range = highlight_range
        self.bg = background_style
        self.width = width

    def __rich_console__(self, console, options: ConsoleOptions) -> RenderResult:
        width = self.width or options.max_width
        _, end = self.range                      # 0..width 的浮点填充位
        safe = min(end, 0.4 * width)             # 阈值：如 20G/50G=0.4
        t = Text(end="")
        t.append("━" * int(safe), style="green")
        t.append("━" * int(max(0.0, end - safe)), style="red")
        t.append("━" * int(max(0.0, width - end)), style=self.bg)
        yield t

class TrafficBar(ProgressBar):
    BAR_RENDERABLE = TwoToneBar

pb = TrafficBar(total=50.0)          # 单位 GB
pb.update(progress=used)             # 注意 update(total=..., progress=..., advance=...) 均为关键字参数
```

**方案 B（更简单）：纯 Container 自绘双色条**（完全避开渲染钩子，样式全 CSS 控制）：

```python
class DualBar(Horizontal):
    """按百分比拆三段的自绘进度条。"""
    def __init__(self, *, limit_ratio=0.4, id=None):
        super().__init__(id=id)
        self.add_class("dual-bar")
        self._safe = Static(classes="seg safe")
        self._over = Static(classes="seg over")
        self._rest = Static(classes="seg rest")
        self._total = 0.0

    def compose(self):
        yield self._safe
        yield self._over
        yield self._rest

    def update(self, used: float, limit: float, total: float) -> None:
        self._total = max(total, used, 1e-9)
        safe_w = min(used, limit) / self._total * 100
        over_w = max(0.0, used - limit) / self._total * 100
        rest_w = max(0.0, 100.0 - safe_w - over_w)
        self._safe.styles.width = f"{safe_w:.2f}%"
        self._over.styles.width = f"{over_w:.2f}%"
        self._rest.styles.width = f"{rest_w:.2f}%"
```

```tcss
.dual-bar { height: 1; }
.dual-bar .safe { background: $success; }
.dual-bar .over { background: $error; }
.dual-bar .rest { background: $panel; }
```

**陷阱**：
- 方案 A 里自定义 renderable 的 `__init__` 签名必须兼容 `Bar.render()` 的 kwargs（`highlight_range/highlight_style/background_style/gradient/width/clickable_ranges` 都会以关键字传入）；
- `show_bar/show_percentage/show_eta` 只在 `compose` 读取一次，挂载后改不会重建子件；
- 内置 Bar 的宽度量化是半格（`╺━╸`），自定义时按整数格取整即可；
- 方案 B 的 `width: "xx%"` 赋值合法（scalar 字符串），但三段和可能因取整差 1 格，给 `.rest` 用 `width: 1fr` 兜底更稳。

### 3.3 月份选择器

**需求**：账单/流量屏切换 `YYYY-MM`。

**支持性**：
- `Select(options, *, prompt, allow_blank, value, type_to_search=True, compact=False, tooltip)`（`_select.py:282` 起签名验证）——支持输入即搜索（type-to-search）；**更新选项只能 `set_options(...)` 整体替换**（`_select.py:559`，无 `add_option`）；消息 `Select.Changed`；
- `OptionList`（`ScrollView`）：`add_option/add_options/remove_option/get_option...`（源码 362/414/429/599 行验证），消息 `OptionHighlighted / OptionSelected`；
- "左右箭头 + 文本"组合：纯 `Button/Static + reactive` 自组。

**推荐**：12×N 的月份范围固定、需要键盘快切 → **组合式 MonthPicker**（语义直接、无下拉、支持 `<`/`>` 快捷键）；月份跨度大时退而用 Select。实现：

```python
from textual import on
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Static

class MonthPicker(Widget):
    """◀ YYYY-MM ▶ 组合月份选择器；值变化发 MonthPicker.Changed(value)。"""

    class Changed(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    value = reactive("2026-01")

    DEFAULT_CSS = """
    MonthPicker { layout: horizontal; height: auto; }
    MonthPicker Button { width: 3; height: 1; padding: 0; }
    MonthPicker .cur { width: auto; min-width: 9; content-align: center middle; }
    """
    BINDINGS = [Binding("left", "month(-1)", show=False),
                Binding("right", "month(1)", show=False)]

    def __init__(self, months: list[str], value: str | None = None):
        super().__init__()
        self.months = months
        if value:
            self.value = value

    def compose(self):
        yield Button("◀", id="prev")
        yield Static(self.value, classes="cur", id="cur")
        yield Button("▶", id="next")

    def watch_value(self, v: str) -> None:
        if self.is_mounted:
            self.query_one("#cur", Static).update(v)
            self.post_message(self.Changed(v))

    def action_month(self, delta: int) -> None:
        i = self.months.index(self.value)
        self.value = self.months[max(0, min(len(self.months) - 1, i + delta))]

    @on(Button.Pressed, "#prev")
    def _prev(self): self.action_month(-1)

    @on(Button.Pressed, "#next")
    def _next(self): self.action_month(1)
```

**陷阱**：
- `Select` 展开的下拉是内部 `SelectOverlay(OptionList)`，最大高度受屏幕限制，12 个月没问题；但它吃掉 Enter 用于展开/确认，外层若也绑 Enter 会先被 Select 消费；
- OptionList 做"单选月份列"时要自己限制 `height`（如 `height: 13`），否则 `1fr` 撑开整页；
- `watch_value` 里 `self.query_one` 前必须判 `is_mounted`。

### 3.4 模态：ModalScreen 三段式 与 页面内浮层

**需求 a**：居中面板 + 顶栏标题 / 内容区 / 底栏按钮。
**需求 b**：浮层嵌在页面内部，不覆盖全屏。

**支持性 a：完全支持（即项目 widgets.py 模式）**。`ModalScreen`（`screen.py:2158`）默认 CSS：`layout: vertical; overflow-y: auto; background: $background 60%;`——**半透明背景即"Dim"是内置行为**，下层画面透出来；把面板做成三段即可：

```tcss
MyModal { align: center middle; }
MyModal > .modal-box {
    width: 90; max-height: 90%; height: auto;      /* 居中由父 Screen 的 align 完成 */
    layout: vertical; border: round $primary; background: $surface;
}
MyModal .m-head { height: auto; padding: 1 2; text-style: bold; }
MyModal .m-body { height: 1fr; overflow-y: auto; border-top: solid $secondary; }
MyModal .m-foot { height: auto; align-horizontal: right; dock: bottom; padding: 1 2; }
```

`m-foot` 用 `dock: bottom` 可保证内容超长时按钮仍钉在面板底部（`.m-body` 需配合 `overflow-y: auto`）。ModalScreen 的绑定链在 modal 处截断（`_modal_binding_chain`，实测：下层 Screen 的 priority 绑定在 modal 内不再生效），但 **App 的 priority 绑定仍然优先**（实测 modal 中 ctrl+q 仍退出）。

**支持性 b：支持，用层/绝对定位**。8.2.8 CSS：`position: relative|absolute`、`offset: <x> <y>`（整数，可负）、`layer: <name>`、`Screen { layers: a b; }`（层栈，后者在上）、`overlay: none|screen`（`css/types.py::Overlay = Literal["none","screen"]`）。`overlay: screen` 在合成器里以 `no_clip` + 最高绘制序入栈（`_compositor.py:685`）：**不受父容器裁剪、恒盖在普通内容之上**，且命中测试同样按此层处理（实测 `get_widget_at` 返回浮层内的 Button）。

```python
class InlineLayerApp(App):
    CSS = """
    Screen { layout: vertical; layers: base float; }
    #content { height: 1fr; }
    #float {
        display: none;                /* 用类切换: .open { display: block; } */
        position: absolute;
        offset: 0 -100%;              /* 负偏移 + overlay 实现"贴某元素上方" */
        overlay: screen;
        layer: float;
        width: 40; height: 10;
        border: thick $accent; background: $surface;
    }
    """
    def compose(self):
        yield Vertical(Static("表格/内容…"), id="content")
        yield Vertical(Static("详情"), id="float")

    def action_show(self):
        f = self.query_one("#float"); f.display = True; f.focus()
```

实测结论：hidden 时 `get_widget_at` 返回 `bg`，`display: block` 后同坐标返回浮层内控件——**页面内浮层真实可用**。

**陷阱**：
- **没有 `z-index` / `top` / `left`**（实测 CSS 解析直接报 Invalid CSS property）；层叠顺序由 `layers`+`layer` 与 DOM 顺序决定（同层后挂载者在上）；
- `overlay: screen` 让 widget 忽略父级裁剪，但也意味着它**跟随所在 Screen 不随父容器滚动**——放在 ScrollView 内的浮层要自己维护位置（用 `screen.find_widget(anchor).region` 计算 offset）；
- 浮层不会自动接管 Esc / 失焦关闭：需在宿主 Screen 上绑 `Binding("escape", "close_float")`，或用 `DescendantFocus` 事件判断焦点离开后隐藏；
- 焦点：浮层内控件要先 `display = True` 再 `focus()`，否则 display:none 无法聚焦；
- 真正需要键盘模态语义时仍建议 ModalScreen；浮层适合"内联详情 / 行内预览 / 下拉式面板"。

---

## 4. 键盘体系（Bindings）

### 4.1 体系与键名验证

**Binding 字段**（`binding.py:55-105` 验证）：`key`（逗号分隔多键别名，如 `"home,ctrl+a"`）、`action`（可带参数 `"month(-1)"`）、`description`、`show`（进 Footer/按键帮助）、`key_display`、`priority`、`tooltip`、`id`（配合 `App.set_keymap` 让用户改键）、`system`、`group=Binding.Group(description, compact)`（Footer 分组显示）。

**层级与派发顺序**（源码 `app.py::_check_bindings`、`screen.py::_binding_chain/_modal_binding_chain` + 实测）：
1. **priority=True**：`App → … → Screen → … → 焦点 widget` 反查链顶端，最先截获；实测 Screen 级 `ctrl+r priority` 在 Input 聚焦时仍触发；
2. **普通绑定**：从焦点 widget 向上 `→ 祖先容器 → Screen →（非 modal 时）App`；有 ModalScreen 激活时**链在 modal 处截断**，下层 Screen 与 App 的普通绑定失效；
3. 都未命中 → `dispatch_key`：沿焦点链调用 `key_<键名>` / `on_key` 方法（实测 Screen 的 `key_r` 模式可用）；
4. 动作抛 `textual.actions.SkipAction` 视为未消费，继续向上传（滚动类 action 即如此实现）；
5. `check_action(action, params)`（`dom.py:1909`）返回 False 可让绑定**显示但不触发**（Footer 置灰）。

**键名逐一验证**（`textual/keys.py`，8.2.8 全部存在）：

| 键 | keys.py 常量 | 备注 |
| :- | :- | :- |
| `ctrl+r` `ctrl+q` `ctrl+n` `ctrl+e` `ctrl+d` | ControlR/Q/N/E/D | 均在 `ControlA..ControlZ` 全集 |
| `home` `end` `pageup` `pagedown` | Home/End/PageUp/PageDown | 另有 `ctrl+home`、`shift+pageup` 等组合 |
| `f2` | F2（F1–F24 全系） | |
| `escape` | Escape | `super+c` 等新平台键也存在 |
| `enter` `tab` | Enter/Tab | `shift+tab` 别名 BackTab |

**默认占用（务必避开/知悉）**：`ctrl+q`=App 默认退出（priority）；`ctrl+p`=命令面板（`App.ENABLE_COMMAND_PALETTE=False` 或 `COMMAND_PALETTE_BINDING` 可改）；`ctrl+c`=复制文本（8.x 起不再退出，按了只会收到"想退出请按 ctrl+q"的 notify）；`tab/shift+tab`=Screen 级 focus_next/previous；`ctrl+home/ctrl+end` 在 DataTable 内是跳顶/底。**`escape` 没有任何默认 pop/关闭行为**（实测 modal 中按 Esc 栈不变；`Screen._key_escape` 仅清除文本选区）——项目各屏自绑 `escape` 是必须的。

**改键**：`App.set_keymap({"binding-id": "keys"})` + `Binding(id=...)`（8.x 特性）；显示名可覆写 `App.get_key_display(binding)`（`app.py:2013`，做中文键名映射）。

**陷阱**：
- 单字母绑定（`r`、`a`）与输入框共存时自动被放行给 Input（见 §4.2），但 **`priority=True` 的打印键绑定在焦点 Input 时会被 `check_consume_key` 过滤掉**（绑定链构建时即删除，见 `_binding_chain` 注释 "Filter out bindings that could be captures by widgets (such as Input, TextArea)"）——想强制拦截打印键只能在 `on_key` 里做；
- Screen/Widget 不要占用 `enter` 作为全局动作的同时期待 Input.Submitted 正常触发：Input 的 `enter` 绑定在焦点链里优先。

### 4.2 Home/End/PgUp/PgDn 默认行为与"焦点在文本框时不拦截"

**默认行为（源码 + 实测）**：

| 焦点 | home/end | pageup/pagedown |
| :- | :- | :- |
| `Input` | 自身绑定 `home,ctrl+a` / `end,ctrl+e` → **光标移动**（实测 cursor 0↔11，外层不滚） | Input 未绑定 → 冒泡，通常无事发生 |
| `ScrollableContainer` | 绑定 `scroll_home/scroll_end`（实测 home→0、end→max） | `page_up/page_down`（实测 5 次 pagedown 后 home 归零） |
| `DataTable` | 绑到 `scroll_home/scroll_end`（表格内水平到最左/最右列）；**跳顶/底是 `ctrl+home/ctrl+end`** | 光标翻页（`action_page_down` 按可视行数移动光标，非纯滚动） |
| 无焦点 | 链 `[Screen, App]`：默认无人绑 home/end → 无动作（实测 scroll_y 不变） | 同左 |

**焦点在文本框时不拦截的可行方案**（全部验证过机制）：
1. Screen 级绑定**不加 priority**：非打印导航键（home/end/pageup…）由 Input 自身绑定优先消费（实测）；
2. 打印键：绑定链自动过滤（`check_consume_key`），Screen 的 `Binding("r", ...)` 在 Input 聚焦时**根本不会注册**，无需手写守卫；项目里 `key_r` 的 `isinstance(self.app.focused, Input)` 守卫属于 handler 方法路径的双保险（`key_r` 方法不受过滤器影响，可保留）；
3. 需要条件禁用时用 `check_action`：`if action == "goto_home" and isinstance(app.focused, Input): return False`。

```python
class Page(Screen):
    BINDINGS = [
        Binding("home", "scroll_top", "顶", show=True),    # 非 priority：Input 聚焦时让位
        Binding("end", "scroll_bottom", "底", show=True),
    ]
```

**陷阱**：
- 想让 home/end 始终归页面：必须 priority=True——但会**同时抢走 Input 的光标键**（实测 priority 对非打印键有效），二者不可兼得，建议不加 priority、改用 `ctrl+home/ctrl+end` 或 `g/G` 做页面跳转；
- `TextArea` 有自己的 home/end 行移动绑定，行为同上。

### 4.3 Tab / Enter / Esc 默认消费

- `tab`/`shift+tab`：`Screen.BINDINGS` 内置 `app.focus_next/previous`（show=False）；Input 不消费 tab → 表单里 tab 直接跳焦点；焦点环顺序 = DOM 顺序（`Widget.can_focus`），容器默认 `can_focus_children=True`；
- `enter`：`Input` → 发 `Submitted`；`DataTable` → `select_cursor`（行模式发 `RowSelected`）；`OptionList` → `OptionSelected`；`Button` → `press`；未聚焦任何可消费者时 enter 无人处理（不弹默认动作）；
- `escape`：见上——**无默认**；另外 `App.ESCAPE_TO_MINIMIZE`/`Screen.ESCAPE_TO_MINIMIZE`：有 maximize 的 widget 时 Esc 先用于 minimize（priority 路径前插入，`app.py:4122-4131`）。

---

## 5. 鼠标

### 5.1 DataTable / OptionList 点击行为（实测）

- **DataTable**：App 合成 `events.Click`（无独立 DoubleClick 事件，用 `Click.chain` 计数，`app.py:4100-4118`）。`_on_click` 逻辑：单击**只移动光标，不发选中消息**；当点击格与当前光标重合时 `_post_selected_message()` → `RowSelected`。实测：第 1 击 cursor→行；第 2 击同格→RowSelected；`pilot.double_click`→cursor+RowSelected 各一；点不同格不触发选中；表头点击发 `HeaderSelected`（列排序挂这个）。
- **OptionList**：单击 = 立即 `highlighted` + `action_select()` → 发 `OptionSelected`（实测 highlight 变 1）。即"单击即选中"，无双击语义。
- **`.instant` 样式类**：**8.2.8 本地源码全库检索不存在**（含 .css/.py，无此机制、无文档字符串）。若诉求是"去掉点击/滚动动画"：`App.animation_level = "none"`（`app.py:838`），按钮按压动画用 `Button.active_effect_duration = 0`；网上教程里的 `instant` 类名不属于本版本，勿抄。

### 5.2 单击选中 → 进入编辑的推荐交互

DataTable 模式与键盘习惯统一：
1. 单击：移动光标（选中态由 `cursor_type="row"` 高亮呈现）；
2. 双击同一行：触发 `RowSelected` → `@on(DataTable.RowSelected)` 打开 `RecordFormModal`（项目现有代码天然兼容，双击即可进入编辑，无需改）；
3. 键盘：Enter 同发 `RowSelected`；`F2` 可另绑"编辑当前行"：`Binding("f2", "edit_row")`（键名已验证）；
4. 需要"单击即开编辑"的（如设置页两行小表），给表加 `Binding` 或直接监听 `CursorMoved`？——8.2.8 无 `CursorMoved` 消息（本版本消息集：`CellHighlighted/RowHighlighted/ColumnHighlighted/CellSelected/RowSelected/ColumnSelected/HeaderSelected/RowLabelSelected`），单击即编辑建议仍走双击/Enter，或挂 `@on(DataTable.RowHighlighted)` 仅做预览。

**陷阱**：`RowHighlighted` 在鼠标 hover/键盘移动时高频触发，里面只做轻量刷新（`row_selected` 才开弹窗）；`row_key` 与 `cursor_row` 都在消息对象上，筛选重排后优先用 `row_key` 反查数据。

---

## 6. 其它

### 6.1 主题（theme）

**支持性**：`App.theme: Reactive[str]`（默认 `textual-dark`，可被环境变量 TEXTUAL_THEME 覆盖，`constants.py:163`）；`App.available_themes`（dict）、`register_theme(Theme)`、`unregister_theme`；实测 `self.theme = "tokyo-night"` 即时换肤。内置 **21** 套（`theme.py::BUILTIN_THEMES`）：textual-dark/light、nord、gruvbox、catppuccin-mocha/frappe/latte/macchiato、dracula、tokyo-night、monokai、flexoki、solarized-light/dark、rose-pine/moon/dawn、atom-one-dark/light、ansi-dark/light。

统一按钮语义色：Button variant 走 `$primary/$success/$warning/$error` 变量，换主题自动全局一致。自定义主题：

```python
from textual.theme import Theme

app.register_theme(Theme(
    name="aliyun",
    primary="#ff6a00",      # Button.-primary / 焦点边框
    secondary="#004578",
    success="#4EBF71", warning="#ffa62b", error="#ba3c5b",
    background="#101014", surface="#1c1c22", panel="#22222a",
    variables={"text-muted": "#8a8a93"},   # 项目 CSS 用到的自定义变量
))
app.theme = "aliyun"
```

**陷阱**：`Theme` 未给的字段由 `ColorSystem` 自动推导（luminosity_spread/text_alpha）；`ansi-dark/light` 会触发 CSS `:ansi` 伪类分支；`App.action_toggle_dark` 在 light/dark 间切（兼容旧代码），主题切换建议自绑 `Binding("t", "toggle_theme")` 前先查与命令面板 `theme` provider 是否冲突；主题变量是字符串模板，保存后 `textual-` 前缀名保留给内置。

### 6.2 等待反馈：loading / notifications / 操作提示栏

- **Spinner 不存在**（8.2.8 无 `textual.spinner` 模块，grep 验证）→ 用 **`LoadingIndicator`**（`widgets/_loading_indicator.py`，已在 `textual.widgets.__all__`）：默认动画为 "dots"，`loading_indicator.start()` 或在 widget 上 `self.loading = True`（`Widget.loading` reactive 会用 `get_loading_widget()` 覆盖整块区域并吞鼠标事件，适合"加载中遮罩"）；
- **通知**：`App.notify(message, *, title, severity, timeout, markup=True)`，severity ∈ `information|warning|error|success`；渲染为 `Toast`；**线程安全**（worker 里直接调用）；项目 notify_ok/err/warn 封装正确，补充：`App.action_notify(message, title, severity)` 可直接作绑定动作；
- **操作提示栏现成件**：
  - `Footer`：自动罗列当前 `show=True` 的活动绑定（含 priority/聚焦变化），构造参数 `compact=False` 可切紧凑样式（`-compact` 类）、`show_command_palette=False` 隐藏右侧 ctrl+p 提示项；绑定描述即提示文案——项目自维护 `.page-hint` 字符串与 Footer 双轨，建议逐步把关键键交给 Footer；
  - `textual.widgets.KeyPanel`（右侧 split 面板，展示焦点 widget 键表）与 `HelpPanel`（App 级 `action_show_help_panel()`，命令面板里 "Keys" 入口即调它）；
  - 动态提示数据源：`App.active_bindings`（dict[key → ActiveBinding]，实测可枚举），自己渲染"键: 说明"行；`Binding.group` 让 Footer 归组显示；
  - 无 `key_display` 小部件导出（组件是 `Footer` 内部 `KeyGroup` 等私有件），别 `from textual.widgets import KeyLine`——本地无此类。

### 6.3 官方文档相关页面（URL 以站点 sitemap 核验存在）

- 指南：`https://textual.textualize.io/guide/app/`、`.../guide/CSS/`（注意大写，`/guide/css/` 404）、`.../guide/layout/`（滚动/尺寸）、`.../guide/events/`、`.../guide/input/`（键鼠+焦点；**无 /guide/keys/，旧链接 404**）、`.../guide/actions/`、`.../guide/reactivity/`、`.../guide/screens/`（Modal/栈/结果）、`.../guide/animation/`（transitions）、`.../guide/content/`（富文本/markup）、`.../guide/styles/`、`.../guide/design/`、`.../guide/devtools/`（`textual run --dev`，CSS 热重载）、`.../guide/command_palette/`、`.../guide/testing/`（`run_test`/Pilot）。
- 组件：`.../widgets/data_table/`、`.../widgets/progress_bar/`、`.../widgets/select/`、`.../widgets/option_list/`、`.../widgets/button/`、`.../widgets/digits/`、`.../widgets/footer/`、`.../widgets/loading_indicator/`、`.../widgets/rule/`、`.../widgets/header/`；总览 `.../widget_gallery/`。
- How-to：`.../how-to/design-a-layout/`、`.../how-to/center-things/`、`.../how-to/work-with-containers/`、`.../how-to/render-and-compose/`。
- 在线内容多为 ≤1.x API，落笔前先用本文方法在本地 site-packages grep 复核（`Gradient(colors=...)`、`z-index/top/left`、`Rule(title=)`、`Spinner`、`from textual.widgets import Option` 等都是新版**不成立**的写法——`Option` 须 `from textual.widgets.option_list import Option`，项目 home.py 已兜底）。

---

## 7. 附：本文验证方法备忘

- 静态：`grep` 本地 `site-packages/textual/`（类名、DEFAULT_CSS、BINDINGS、css/constants.py 枚举）；
- 动态：`/webservices/aliyun-controller/venv/bin/python` 内联 `App.run_test(size=...)`（无文件落盘），关键结论均有对应实测：滚轮命中/冒泡（§1.3）、双滚动条两配置对比（§1.2）、priority/普通绑定在 Input 与 modal 下的行为（§4.1）、home/end 焦点差异（§4.2）、DataTable 单击/双击/OptionList 单击消息（§5.1）、浮层 hit-test 与 z-index/top/left 报错（§3.4）、自定义 BAR_RENDERABLE 渲染（§3.2）、主题清单与切换（§6.1）、`pilot.resize_terminal`→on_resize（§1.4）、键名常量全集（§4.1）。

---

## 8. 第二轮界面打磨实测勘误（8.2.8，全部 run_test 验证）

- **`content-align` 不定位子件**：vertical 布局容器里 `content-align: center middle` 对子 widget 完全无效（首页内容顶左）；让一组 auto 尺寸的子件整体居中要用 **`align: center middle`**（ModalScreen 正是靠它居中）。
- **Textual CSS 不支持相邻兄弟选择器 `+`**：`.a + .a { }` 直接抛 TokenError（"Expected selector or {"）。行距改用显式类（如 `.row.gap-top`）。
- **`max-height: none` 非法**：scalar 属性不接受 `none`，想要不限制就删掉声明。
- **8.2.8 `textual.widgets` 无 `Spacer`**：横向撑开用 `Static(classes="fill")` + `width: 1fr`。
- **`width: 1fr` 与 `%` 混排不可靠**：同一 Horizontal 里 `%` 段 + `1fr` 段时 fr 解析为全宽（进度条溢出）。三段全用 `%`，由计算侧保证和为 100。
- **自定义 Message 想被 `@on(Msg, "#id")` 选择器匹配**：必须暴露 `control` 属性；`Message` 无公开 `sender`，返回 `self._sender`。
- **`Switch`（8.2.8）**：`value` 为 bool（`is True` 可直接比较），单击整个控件即切换（`_on_click → toggle`），Enter/Space 同；适合做表单里的布尔条目。
- **表单弹窗纵向空间**：`> .modal-box { min-height: N }` + body `height: 1fr`，容器被 min-height 撑开时 fr 子项吃到多余空间、`dock: bottom` 按钮栏仍贴底。

### 8.1 单击执行表格（ClickTable 配方）

- `ClickTable(DataTable)` 重写 `on_click`：从 `event.style.meta` 读 `row/column`，命中数据格（`0 <= row < row_count`、`col >= 0`、非 `out_of_bounds`）时 `cursor_coordinate = Coordinate(row, col)` + `_post_selected_message()` 并 `return`；表头/行标签等回落到 `await super()._on_click(event)`。
- 派发顺序依据 `message_pump._get_dispatch_methods`：按 MRO 逐类取 `cls.__dict__["_on_click"] or cls.__dict__["on_click"]`，所以子类的 `on_click` 先执行、原生 `_on_click` 随后（移动光标/表头消息不受影响），键盘 Enter 的 `RowSelected` 语义保留。
- 屏幕级 `on_click`（如 PageScreen 收起浮层）收到的是冒泡 Click，`event.x/y` 为屏幕坐标，配合 `Region.contains(x, y)` 判断点击是否在浮层外。

### 8.2 DataTable 列宽

- `add_column(label, width=int)` → `Column.width` 固定（含左右 padding 各 1）；`width=None` 为 `auto_width`（按内容）。
- 动态铺满：改 `column.width` 后需 `column.auto_width = False` + `table.refresh(layout=True)`（封装为 `widgets.fit_table_columns`）。
- 无列对齐属性：右对齐用前导空格（`rcell`，按 `rich.cells.cell_len` 补位），表头同样处理；`Column.label` 可赋值更新。

### 8.3 DataTable 单击执行的双派发坑

- Textual 沿 MRO 逐类查找事件 handler（`message_pump._get_dispatch_methods`）：子类定义 `on_click`/`_on_click` 后，**父类 `DataTable._on_click` 仍会被派发一次**。重写点击时必须对接管与回落两条路径都 `event.prevent_default()`，否则光标移动/`RowSelected` 双发（表现为 push 两次、需要按两次 Esc）。
- Click/Mouse 事件冒泡到 Screen 时 `event.x/y` 会被改写成相对坐标；判断“点在哪”一律用 `event.screen_x/screen_y`。
- `virtual_size.width` 对垂直滚动条的空间预留时机不稳定（布局前后差 2 cell），列宽铺满后仍可能出现 `max_scroll_x=2`。对策：`.tbl { overflow-x: hidden }` 兜底 + 列宽计算时按 `table.max_scroll_y > 0` 预估预留 2。

### 8.4 页面内浮层（overlay）三要素

1. **父容器（布局上直接包裹浮层的 Widget）必须声明 `layers: base overlay`**——hit-test 的层序取自父容器 styles，缺了就会“看得见但点不到”（点击被下层组件抢走）。
2. 浮层自身：`position: absolute` + `overlay: screen` + `layer: overlay` + `display` 切换；显示后再设 `styles.offset`（`region.width` 在 display 前为 0，用类常量存宽度）。
3. Button 系控件布局步进 = `width + 2*line-pad`（Button 默认 `line-pad:1`、`min-width:16`）。紧凑网格必须同时写 `width/min-width/padding/margin`；`line-pad: 0` 是非法值（最小 1），要 4 列宽 4 的步进就得把容器加宽到 4×(width+2)。

### 8.5 列宽/表头/单元格三者同步（首屏右对齐列偏 2 格根因）

- `max_scroll_y` / `scrollbar_size_vertical` 依赖布局时刻：`load_rows` 前=0、有滚动条后=2，两次 `fit` 结果不同。**只重设列宽+表头而不重建单元格** → 首屏右对齐列（空格 padding 基于旧 vis）与列宽错位；滚动/刷新触发整表重建后“自愈”。
- 规则：垂直滚动条用**行数**确定性预判（`rows > region.height - 3`），列宽、`rcell` 表头、`rcell` 单元格在同一函数内基于同一份 `vis` 生成；布局稳定后用 `call_after_refresh` 再完整重建一次（幂等），禁止局部 patch。
- `Pilot` 无 `shift_tab()`，用 `pilot.press("shift+tab")`。

## 9. 第三轮统一：全局单行按钮与级联陷阱（8.2.8，三项目实测）

### 9.1 Button 全局单行配方

各 `app.tcss` 统一：

```css
Button {
    height: 1;
    border: none;
    min-width: 0;
    padding: 0 1;
}
Button:focus { border: none; text-style: bold reverse; }
Button:disabled { text-opacity: 60%; }
```

- `compact=True` 从此多余（其效果被全局覆盖），调用处不再传；Input/Select 的 compact 仍需显式传。
- `height: 1` 时 border 会吃掉整行，必须 `border: none`；焦点态改用 `text-style: reverse`。
- 变体按钮（primary/error/success/warning）的语义色即背景色，单行下依旧成立。

### 9.2 app CSS 压过 widget DEFAULT_CSS（同属性级联）

- 同一属性上，**app.tcss 的规则始终赢过 widget `DEFAULT_CSS`**，与选择器特异度无关。
- 历史事故：全局 `Button { padding: 0 1 }` 覆盖 MonthPicker `DEFAULT_CSS` 里 `.mp-m { padding: 0; width: 4 }`，内容宽变 0，`rich.chop_cells` `range(0, n, 0)` 崩溃（渲染期 ValueError，不是启动期报错，很难归因）。
- 结论：写全局控件规则前先 grep 各 widget DEFAULT_CSS 中对**该控件**的 `width/padding/margin/height` 定制，并把控件自身宽度改为「文本宽 + padding 2 + line-pad 2」。

### 9.3 后代选择器命中“借住”组件

- `#topbar Button { margin-right: 2 }` 会命中所有渲染进顶栏的按钮——包括顶栏槽位里组件（MonthPicker）的**内部按钮**；id 前缀的特异度还压过组件自己的类规则。
- 顶栏间距规则必须限定到具体 id（`#topbar #top-back, #topbar #top-menu`），不要用 `#topbar <元素>`。

## 10. 跑马灯提示栏与动态 Logo（第三轮优化实测）

### 10.1 跑马灯组件（HintBar）配方

Textual 的 `Static` 在 `width: 100%` 时即便设置 `no_wrap=True` 也会将内容静默换行。实现 LED 跑马灯必须使用 `ScrollableContainer` 包裹 `width: auto` 的 `Static`。

```python
class HintBar(ScrollableContainer):
    DEFAULT_CSS = """
    HintBar { height: 1; overflow-x: auto; overflow-y: hidden; scrollbar-size: 0; }
    HintBar .hint-text { width: auto; height: 1; }
    """
    def _tick(self):
        total, view = self.virtual_size.width, self.size.width
        if total <= view or self.hovered:
            self.scroll_x = 0
            return
        x = int(self.scroll_x) + 1
        if x > total - view + 3: x = 0
        self.scroll_x = x
```

- **容器高度**：在 `app.tcss` 中必须显式设置 `.page-hint { height: 1 }`，否则 `PageScreen` 的 `height: auto` 默认规则会压过组件的 `DEFAULT_CSS`。
- **滚动条隐藏**：`scrollbar-size: 0` 在 8.2.8 中有效，可完全隐藏滚动条但不影响程序化滚动。

### 10.2 动态 Logo 与版本号对齐

为了让版本号始终对齐 Logo 右侧，首页采用了「动态容器宽」策略：

1. `ui.py` 使用 `rich.cells.cell_len` 动态计算 `LOGO_WIDTH`。
2. 首页 `_apply_breakpoint` 时，若非窄终端，显式设置 `self.query_one("#home-col").styles.width = max(LOGO_WIDTH, 40)`。
3. 版本号组件使用 `text-align: right`，从而在不同 Logo 宽度下均能精准对齐。

### 10.3 统一圆角边框

`DataTable` 的 `border: round` 会在 `:focus` 态被默认规则覆盖。必须显式设置：
```css
.tbl:focus { border: round $primary; }
```
从而保证在任何状态下，页面主要容器（`.panel`）与表格（`.tbl`）的视觉风格统一。

## 11. 第四轮打磨实测：Logo 对齐、菜单跑马灯、提示栏回滚（8.2.8）

### 11.1 Logo 逐行错位根因：trailing space + text-align: center

- `Static` 的 `text-align: center` 会**按每行裁剪后的宽度独立居中**。若 `LOGO_LINES` 各行带不同数量的尾随空格（或补空格到统一宽度），中行与底行会被各自居中到不同起点，肉眼即「最底下一行错位」。
- 正确做法：
  1. `logo_text()` 对每行 `rstrip()`（去掉尾随空格）；
  2. `LOGO_WIDTH = max(cell_len(line.rstrip()) for line in LOGO_LINES)`；
  3. `#home-banner { text-align: left; }`，整块靠 `#home-col`（`align: center middle`）居中——所有行共用同一左缘，不再逐行错位。
- litellm 之所以「正常」，是其 logo 各行无尾随空格、宽度一致，逐行居中恰好等价。

### 11.2 版本号对齐 Logo 右缘

`#home-col` 宽度必须在运行时写回：`col.styles.width = max(LOGO_WIDTH, 40)`（`ui.home_col_width()`），版本行 `text-align: right` 即对齐 Logo 右缘。不要用 CSS 写死 72：不同项目 logo 宽度不同（如 55），写死会让版本号离 Logo 右缘很远。

### 11.3 OptionList 菜单行「描述右对齐 + 超宽跑马灯」

- `Option(Text(长文本))` 默认会**换行**撑高选项。要单行显示必须 `Text(..., no_wrap=True)`（可配 `overflow="crop"` / `"ellipsis"`）。
- OptionList 可容纳宽度 = `content_size.width - (show_vertical_scrollbar ? scrollbar_size_vertical : 0)`；选项首列还有 2 格内边距。
- 右对齐：`" " * (room - cell_len(desc)) + desc`；超宽：`marquee_window()` 取循环窗口 + 定时 `replace_option_prompt_at_index()`（**不会重置 highlighted**）。
- 封装为 `widgets.MenuMarquee`：标签 `shorten(..., label_width)` 定宽左对齐，描述区域右对齐 / 来回跑马灯；`set_rows()` 支持数据变化后刷新。

### 11.4 HintBar 来回滚动

单向循环改为「到达端点后 `HOLD` 拍（12 × 0.25s ≈ 3s）再反向」，用 `_direction` + `_hold` 两个状态位即可；`update()` 时重置方向与停留。

### 11.5 容器内的 OptionList 必须去掉默认边框

`OptionList.DEFAULT_CSS` 自带 `border: tall $border-blurred`（直角框）。放进 `.panel` 圆角容器时必须显式 `border: none; background: transparent`，否则圆角容器内套一个直角方框（历史案例：litellm `#mh-list`）。

---

## 12. 加载指示器与遮罩实测记录

### 12.1 `Widget.loading` 与 `ProgressCover`
- Textual 8.2.8 每个 Widget（含 `DataTable`、`SelectionList`、`VerticalScroll`）自带 `.loading` reactive。
- 设 `.loading = True` 时调用 `set_loading(True)`：先 `get_loading_widget()` 取覆盖控件，再加 `-textual-loading-indicator` 类并 `_cover()`。
- **关键实测**：`_cover()` 只是把覆盖控件交给合成器**替换原控件区域**，并不会把它挂进 DOM 参与布局（`widget._parent` 被直接赋值，但不走 `mount`）。因此：
  - 被覆盖控件从命中测试中排除，**鼠标点击天然无法穿透到底层**，解决了加载中误触旧数据行的高频 Bug；
  - **覆盖控件必须是单个自渲染 widget**。若用容器 + 子件（如 `Vertical` 里放 `ProgressBar`/`LoadingIndicator`），子件 `region` 恒为 `Region(0,0,0,0)`，只显示容器自身的边框/背景而内容全空。注意 `ProgressBar` 自身 `compose` 出 `Bar` 子件，所以不能直接拿它当覆盖层。
- 本项目 `ProgressCover(Widget)` 的做法：`render()` 返回一条**固定 40 格、居中**的不定进度条 `Text`（`auto_refresh = 1/15` 逐帧移动高亮；宽度与全屏 `BusyOverlay` 的 `.busy-box` 内容宽一致，由 `content-align: center middle` 居中），组件色用 `COMPONENT_CLASSES = {"pc--bar", "pc--highlight"}`；因为覆盖的是整块（含原控件边框区域），需自带 `border` 与 `margin` 才与被覆盖控件观感一致。
- 覆盖控件撤下后原控件区域宽可能变化，需要在 `table.loading = False` 后 `self.call_after_refresh(self._refit)` 按最终宽度重排，否则列宽按旧宽度算、无法铺满。

### 12.2 全屏 BusyOverlay 模态遮罩
- 继承 `ModalScreen[None]`，屏本体 `align: center middle; background: $boost`；卡片 `.busy-box` 固定 `width: 46`、`height: auto`、`min-height: 5`，内部固定「标题 / 进度条 / 计数行」三行。
- 三种模式：`dots`（`LoadingIndicator`）、`bar`（`ProgressBar(total=None)` indeterminate，逐帧 `auto_refresh=1/15`）、`percent`（`ProgressBar(total=N)` + `#busy-counter`）。
- **布局实测**：`width: auto` + 子件 `width: 100%` 会形成循环约束，首帧只按文本宽渲染，待 `advance()` 触发重排后进度条才出现、容器随之变宽变位；宽字符 Emoji（📡）也会被挤到下一行。改为**定宽 + 标题 `text-wrap: nowrap` + `text-overflow: ellipsis; overflow: hidden` + 三行结构**后，从第一帧起尺寸/位置稳定，超长文案（如 URL）也会以 `…` 截断而顶不破边框。
- `percent` 模式由 `advance(amount)` 推进并刷新 `#busy-counter` 的 `X/Y (pct%)`。在 worker 线程中被调用时，通过 `self.call_from_thread(ov.advance)` 投回 UI 线程。

### 12.3 状态栏点阵 Spinner
- 在 `StatusBar` 内通过 `set_interval(_DOTS["interval"]/1000)` 驱动 Braille 点阵字符（`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` @80ms）逐帧变化。
- 单字符点阵动画完全不改变状态栏高度（高度保持 1），适合轻量、无需全屏遮罩的后台请求。但**操作过快时只在 1 帧内起落、肉眼看不见**；需要可靠可感反馈时改用控件就地忙碌态（见 §12.4）。

### 12.4 控件就地忙碌态与「快速操作看不见 / 覆盖层卡住」问题
- 本地 API 常在 1 帧内返回：无论 `.loading` 覆盖层还是状态栏 spinner，都可能在首帧绘制前就完成，表现为「数据直接蹦出来」。对策是给忙碌态一个**最小可见时长**。
- 覆盖层**由 worker 自己置位与撤下**，不要在 `on_mount` 里同步预置 `loading = True`：
  - 预置后若 worker 因 `_busy`/未挂载而早退，就没人撤除它，而后续轮询都是 `show_overlay=False` 也不会撤 → **覆盖层永久卡住**；
  - 反之若让早退分支去撤，快速操作又会立刻撤掉、仍看不见。
- worker 的正确写法：开头 `if self._busy or not self.is_mounted: return`；进入后 `started = time.monotonic()`、置 `table.loading = True`；`finally` 里 `await hold_busy(started)`（`BUSY_MIN_SECONDS = 0.3`）补足最小可见时长，且用 `try/finally` 包住以确保 `table.loading = False` 在 worker 被取消时也会执行。
- 覆盖层撤下后原控件区域宽可能变化，需要 `self.call_after_refresh(self._refit)` 按最终宽度重排，否则列宽按旧宽度算、无法铺满。
- 对策二（就地、非阻断）：`ControlBusy` 在异步下发期间 `control.disabled = True`，并把触发控件文本换成点阵 spinner（`Button` 改 `label`；`Select`/`Switch` 改同行标签 `Static`），完成后复原。比顶栏 spinner 更直观。

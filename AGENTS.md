# AGENTS.md — clash-controller

# 第一部分 · UI 开发统一规范

> 本部分在 aliyun-controller、`../clash-controller`、`../litellm-controller` 三项目间**保持同一份文本**（个别差异会显式标注）。任何一侧修订必须同步其余项目，保证各项目的 Agent 拿到相同标准。
> 依据均为本地 Textual 8.2.8 实测结论，逐条验证记录见 `docs/textual-dev-guide.md`。

## 1. 技术栈与代码质量

- Python >= 3.10；`textual >= 8.0`（依赖 compact Input、`overlay: screen` 等 8.x 特性）；UI 用到新特性时同步抬版本下限。
- 入口命令 `<pkg>ctl`，支持 `-D/--dir`（指定配置目录，main 在 import app 前写环境变量）与 `--version`。
- ruff：`line-length = 120`，`select = ["E","F","W","I","UP","B","C4"]`，忽略 `E501`；改动后必须零告警。
- 应用代码**禁止 `print`**（破坏 TUI）；调试信息走 `app.add_log` / 状态栏。
- 仓库不提交测试文件；验证用 `/tmp` 一次性脚本（模板见第二部分「验证」）。

## 2. 分层架构

- 展示层：`screens/`、`ui.py`、`widgets.py`、`app.tcss`。`ui.py` 只放可复用界面组件与页面骨架，不写业务取数。
- 服务层：顶层模块（不建 `modules/` 子包），零 Textual 依赖、不 print；失败统一抛项目异常类型（如 `AliyunApiError` / `RuntimeError`），由屏幕层捕获后 `notify_err`。
- 屏幕层通过 App 的懒加载 getter 拿客户端（`app.get_billing()` 模式）。

## 3. App 级约定

- 无 `Header` / `Footer`；App `compose()` 只 yield 一个 `#status`（`height: 1; dock: bottom`）。
- `ENABLE_COMMAND_PALETTE = False`。
- App 绑定 `Binding("ctrl+q", "quit", priority=True)`——任意焦点/输入态都能退出。
- 状态栏 API：`app.status(msg, kind)`，`kind ∈ "" | ok | warn | err`（对应绿/黄/红着色）；`app.notify_ok/notify_err/notify_warn`（status + 右下角 toast）。
- 不要用 `App` 已有只读属性名（`debug` 等）作实例属性（用 `debug_mode` 之类）。

## 4. 页面骨架（ui.PageScreen）

- 功能页一律继承 `ui.PageScreen`，结构固定：
  顶栏 `#topbar`（左 `◀ 返回` 按钮 + Title/SubTitle 靠左；右侧 `compose_toolbar()` 控件 + 可选折叠菜单按钮）→ `#page`（`height: 1fr; overflow-y: hidden`）→ 底部 `.page-hint` 提示栏贴底。
- 子类只写：`TITLE` / `HINT`（及可选 `SUBTITLE`）、`compose_page()`、`compose_toolbar()`、`reload_page()`；键位/Esc/滚动自动获得。
- 副标题动态更新用 `set_subtitle()`；根屏（启动页/选择页）若需与首页一致的居中版式，采用 `Screen` + `.logo-page`（`ui.LOGO_LINES` + `OptionList`）；亦可复用 PageScreen 覆写 `action_page_back()` 为 `app.exit()`，并把返回按钮 label 改成 `◀ 退出`。
- 折叠菜单 `self.menu = [(label|callable, callback, danger)]`：语义对齐 Android ⋮，**只放次要/低频项**；主要操作必须以容器内按钮常驻可见；`menu` 为空时菜单按钮自动不渲染；危险项文本染红。
- 首页：`ui.LOGO_LINES` 艺术字（`#home-banner` **`text-align: left`**，整块靠 `#home-col` 居中，避免逐行居中造成错位）+ 版本副标题右对齐 + 菜单项 + 底部提示栏。列宽由 `ui.home_col_width()`（= `max(LOGO_WIDTH, 40)`，`LOGO_WIDTH` 由 `cell_len(line.rstrip())` 自动计算）在 `on_resize` 动态写回 `#home-col`，版本号因此始终对齐 Logo 右缘。窄终端 `width < LOGO_WIDTH + 6` 挂 `-sm` 降级为普通文本。整组居中用容器 `align: center middle`（`content-align` 不定位子件）。
- 首页菜单描述**右对齐**；描述超宽时用 `widgets.MenuMarquee` 在选项区域内来回跑马灯（不换行）。

## 5. 内容区版式与滚动

- 主模式「**小容器 + 大容器**」：小容器 `.panel`（`padding: 1 2; border: round`）或容器内 `.filter-row` 行放筛选/排序/主操作按钮；大容器是表格（`make_table()` 自带 `.tbl` 类 → `height: 1fr`）。排列方向可变（上大下小 / 上小下大 / 左右）。
- **一切内容皆入容器**：功能页所有可见组件必须包裹在 `.panel` 或 `.tbl` 内，严禁直接在 `#page` 下漂浮。
- 只读状态行用 `.kv-row/.kv-label/.kv-value`（标签定宽、值加粗）。
- **外层永不滚动**：`#page` overflow hidden；固定行 `height: auto`。`Vertical`/`Horizontal` 默认 `height: 1fr`，放进 auto 上下文必须显式 `height: auto`。全页只允许表格一个滚动条。
- Home/End/PgUp/PgDn 由 PageScreen 绑定（不加 priority，Input 聚焦时让位），目标 `_scroller()`：优先 `#page` 内第一个 VerticalScroll，否则 `#page`；表格滚动走 DataTable 自身键位。

## 6. 模态（widgets.py）

- **`Button` 全局单行**：各项目 `app.tcss` 统一 `Button { height: 1; border: none; min-width: 0; padding: 0 1 }` + `Button:focus { text-style: bold reverse }`（等价于旧 `compact=True`，调用处不再传）；变体色（primary/error/…）即背景色。**同属性下 app.tcss 元素规则会压过一切 widget `DEFAULT_CSS` 类/ID 规则**，加全局控件样式前必须审查小尺寸控件（见 §12）。
- 全部模态为 `ModalScreen[T]`，统一三段式：`.modal-title`（`border-bottom: hkey`）→ 内容区 → `.btn-row`（`border-top: hkey; dock: bottom; align-horizontal: right`）。
- 按钮语义：default=一般、primary=推荐、error=危险；顺序 `[取消] [主操作] … [危险放最右]`。
- `ConfirmModal`：回车=确认；不可逆操作 `default_yes=False`（确认按钮变 error 色、回车不再直接放行按钮，仅按钮点击生效）。
- `InputModal`：单行 + validator，留空=不更改语义由调用方决定；`[取消][确定]`。
- `FormModal`：字段 `kind: text | password | choice | switch | note`（clash/litellm 另有 `textarea` 供长文本如私钥；litellm 另有 `browse`：Input + 「选择…」弹 `PickModal` 回填）。
- 字段过多的复合表单（如模型参数、路由组成员勾选）用 **sheet 式模态**：`ModalScreen` + `.modal-box` 定宽（≤104）/ `height 88~90%` + `.modal-title` + `VerticalScroll` 内容 + docked `.btn-row`；不拆多步问询弹窗串联。Input/Select 一律 `compact=True`；`.frm-row { margin-bottom: 1 }`、无 hint 行高恰好 1；`.modal-box { min-height: 24 }`；底部错误行 `#frm-error`（validator 失败提示且不关闭）。
  - 密码框：占位符使用 `widgets.mask_secret()` 脱敏显示，**留空 = 保持原值**。
  - 布尔开关：表单内 `kind="switch"`（Switch 用 `border: none; height: 1` 压单行），**不要**独立动作按钮、不要 Checkbox。
  - **`Switch` / `Checkbox` / `RadioButton` 一律全局压成一行**（`height: 1; border: none; padding: 0 1`，focus 用 background-tint）：litellm 在 `app.tcss` 全局规则；aliyun/clash 在 `.frm-field` / `.kv-row` 上下文同名规则。
  - `extra_buttons`（按按钮 id 返回结果）与功能型按钮必须互斥：如 litellm 的 `browse`「选择…」按钮在 `on_button_pressed` 里按 `.frm-browse` 类名排除，否则会被当额外按钮误 dismiss（历史事故：编辑 Upstream provider 崩溃）。
  - 焦点：`on_mount` 聚焦第一个字段；Tab/Shift+Tab 轮切（Screen 原生焦点链）；Enter（Input.Submitted）= 提交；Esc / Ctrl+C = 取消。
- `OutputModal`：只读长文本，VerticalScroll + `[关闭]`。
- **不要用行内 `display` 切换做就地编辑**——隐藏 Input 不进 Tab 焦点链、编辑态难以退出（已被否，用 FormModal）。
- 调用：worker 内 `await app.push_screen_wait(modal)`；事件回调里 `app.push_screen(modal, callback)`。

## 7. 键位总表（统一，勿另起方案）

| 键 | 行为 |
|---|---|
| `↑↓←→` | 移动焦点/光标 |
| `Tab` / `Shift+Tab` | 轮切焦点（表单内轮切字段） |
| `Enter` | 确认 / 提交 / 执行所选行 |
| `Esc` / `Ctrl+C` | 关闭层级：**浮层 > 模态 > 返回上一页**；**根屏 / 主菜单为退出程序**（Textual 无默认 Esc，每屏必须自绑） |
| `Ctrl+Q` | **任意界面**直接退出程序（App priority 绑定，浮层 / 模态 / 输入态均生效） |
| `Home/End/PgUp/PgDn` | 功能页（PageScreen）滚动（不抢 Input 的文本导航） |
| `Ctrl+R` | 功能页刷新（调 `reload_page()`） |
| `Ctrl+N` / `Ctrl+E` / `Ctrl+D` | 条目页 新增 / 编辑 / 删除 |
| 单字母 `1-9` | 仅限**无输入框的主菜单 / 列表页**（快捷直达） |
| 单字母 `/` | 仅限**确有搜索框的列表页**（聚焦搜索；无搜索框的项目不设） |

- **无 `q` 绑定**：退出统一走 `Ctrl+Q`（任意界面）与 `Esc`/`Ctrl+C`（根屏 / 主菜单）。
- **提示栏只写本页真实有效的键**：`.page-hint` / `HINT` 必须与本页 `BINDINGS` + 继承绑定逐条对应，不得出现该页不可用的键。
- `Ctrl+C` 在 `Input` / `TextArea` 聚焦时仍是「复制文本」（Textual 框架行为），其余位置等价于 `Esc`。

### 7.1 三项目键位设计对照

> 原则：能对齐就对齐；确无对应操作/确实多出操作的，允许差异并在此登记。

| 场景 / 页面 | aliyun-controller | clash-controller | litellm-controller |
|---|---|---|---|
| App 全局 | `Ctrl+Q` 任意退出；`Tab/Shift+Tab` 焦点轮切 | 同左 | 同左 |
| 功能页基类 PageScreen | `Esc`/`Ctrl+C` 返回、`Ctrl+R` 刷新、`Home/End/PgUp/PgDn` 滚动 | 同左 | 同左 |
| 首启 / 根屏 | `SetupWizardScreen`：`Enter` 提交、`Esc`/`Ctrl+C` 取消并退出 | `ProfileListScreen`：`↑↓` 移动、`Enter`/单击 连接、`1-9` 直连、`Ctrl+N/E/D` 增删改、`Ctrl+R` 刷新、`Esc`/`Ctrl+C` 退出 | `SetupWizardScreen`（模态向导）：`Enter` 提交、`Esc`/`Ctrl+C` 取消并退出 |
| 主菜单 | `HomeScreen`：`↑↓`、`Enter`、`1-5` 直达、`Esc`/`Ctrl+C` 退出 | `HomeScreen`：`↑↓`、`Enter`、`1-4` 直达、`Esc`/`Ctrl+C` 退出（切换端点走菜单项 `4`） | `HomeScreen`：`↑↓`、`Enter`、`1-4` 直达、`Esc`/`Ctrl+C` 退出 |
| 列表页（带搜索） | `DomainListScreen` / `RecordListScreen`：`↑↓`、`Enter`/单击、`/` 搜索、`Ctrl+N/E/D`、`Ctrl+R`、`Esc` 返回 | `ConfigScreen`：同左但无搜索框故无 `/` | `ModelListScreen` / `RoutingListScreen`：`↑↓`、`Enter`/单击、`/` 搜索、`Ctrl+N`、`Ctrl+R`、`Esc` 返回；`DefaultEditorScreen`：`Ctrl+N`＋[保存/重置]按钮 |
| 监控 / 查看 | `TrafficScreen` / `SummaryScreen`：`◀▶` 切月、`Ctrl+R`、`Esc` 返回、滚轮浏览 | `OverviewScreen`：`↑↓/PgUp/PgDn` 滚动、`Ctrl+R`、`Esc` 返回（无账期故无 `◀▶`） | `BuildScreen`：`Ctrl+R` 重新构建、[导出 JSON] 按钮、`Esc` 返回 |
| 设置 | `SettingsScreen`：点击条目/`Enter` 开 FormModal 保存、`Esc` 返回 | `SettingsScreen`：`Tab` 轮切、`Enter` 操作控件即时生效、`Ctrl+R`（无保存动作） | `SettingsScreen`：单击/`Ctrl+E` 编辑 Upstream、`Ctrl+N/D`、`Esc` 返回；`ScriptsScreen`：`/` 搜索、`Ctrl+N` 新建、单击 开脚本设置 |
| 日志 | ➖ 无独立日志页 | `LogScreen`：`↑↓/PgUp/PgDn` 滚动、`Ctrl+R` 重载、`Esc` 返回 | ➖ 无独立日志页 |
| 模态 | `Enter` 提交/确认、`Esc`/`Ctrl+C` 取消关闭、`Tab` 轮切字段 | 同左 | 同左；另有 `PickModal`（输入筛选 + 回车）与 sheet 式大表单（`ModelFormScreen` 等，按钮提交，无隐式回车） |
| 文本编辑（Input/TextArea） | 控件原生编辑键优先；`Ctrl+R` 刷新禁用；底部提示栏切 `EDIT_HINT` | 同左 | 同左 |

- 单字母键（`1-9`/`/`）只在**无输入框**页面保留；有 Input 的页面一律用 `Ctrl` 组合键或方向键。
- `1-9` 的位数按各项目菜单/列表实际条目上限确定（aliyun 1-5、clash 首页 1-4 / 端点页 1-9、litellm 首页 1-4 / 参数主页 1-3）。
- litellm 有搜索框的列表页（`ScriptsScreen`/`DefaultEditorScreen`）只用 `Ctrl+N` 与容器内按钮，不再占用会被 Input 抢占的 `Ctrl+E`/`Ctrl+D`（§7.2 约束）。

### 7.2 文本编辑模式（焦点在 Input / TextArea）

- 焦点进入 `Input` / `TextArea` 即视为**文本编辑模式**：控件原生编辑键位优先于页面 / App 的普通绑定（Textual 绑定链按「焦点控件 → 祖先 → Screen → App」查找），无需额外代码即生效。
- Textual 内置编辑键（勿重复实现）：`←→`/`Ctrl+←→`、`Home`/`Ctrl+A` 行首、`End`/`Ctrl+E` 行尾、`Ctrl+Shift+A` 全选、`Shift+方向` 选择、`Backspace`、`Delete`/`Ctrl+D`、`Ctrl+W/U/K` 删词 / 清左 / 清右、`Ctrl+X/C/V` 剪切 / 复制 / 粘贴；`TextArea` 另有 `↑↓`、`PgUp/PgDn`、`Ctrl+Z/Y` 撤销 / 重做、`Ctrl+Shift+K` 删行。
- 冲突规则（统一约定）：
  - **编辑态禁用页面刷新 `Ctrl+R`**：`PageScreen.check_action` 在焦点为 `Input`/`TextArea` 时对 `refresh_page` 返回 `False`。
  - `Ctrl+C`：有选中 → 复制；无选中 → `Input.action_copy` 抛 `SkipAction` 冒泡为「返回 / 取消」（无需额外代码）。
  - `PgUp/PgDn`：保留控件原生行为（`Input` 未绑则冒泡页面滚动，`TextArea` 为光标翻页）。
  - `Tab`/`Shift+Tab`：保留焦点轮切（`TextArea` 保持默认 `tab_behavior="focus"`；改 `"indent"` 会吞 `Tab`，不建议）。
  - `Ctrl+Q`：不受编辑模式影响，任意界面退出。
  - 终端限制：`Ctrl+V` 走 Textual 剪贴板（OSC52），不保证系统剪贴板；`Ctrl+A` 是**行首**（全选是 `Ctrl+Shift+A`）。
- 底部提示栏联动：焦点进出输入框时，页面 `#page-hint` 在 `HINT` 与 `EDIT_HINT` 间切换（`ui.PageScreen._refresh_hint`）；模态（`FormModal`/`InputModal`）在 `#frm-hint`/`#im-hint` 按焦点类型显示 `HINT_FORM`（非输入）/ `HINT_FORM_EDIT`（Input）/ `HINT_FORM_TA`（TextArea）。常量定义在 `widgets.py`。
- **约束**：有搜索框（Input）的列表页不要用 `Ctrl+E`/`Ctrl+D` 做条目操作（会被 Input 的「行尾 / 右删」抢占），改用 `Ctrl+N` 与容器内按钮或 `Enter`。

## 8. 鼠标交互

- **所有可操作行一律单击执行**：数据表用 `widgets.ClickTable`（`make_table()` 默认返回）。单击数据格 = 移光标 + 立即发 `RowSelected`；表头点击仍是 `HeaderSelected`。
- 按钮/选项/入口同样单击触发；滚轮天然作用于光标下滚动容器。
- 行点击判定、浮层外点击判定一律用 `event.screen_x / screen_y`（冒泡到 Screen 的 `event.x/y` 是相对坐标）。
- 单击即执行的高危操作必须仍有 ConfirmModal 把关。

## 9. 表格规范

- 数据展示一律 `DataTable` + `make_table()` / `load_rows()`；禁止手写空格对齐字符串伪表格。
- 内容列 `shorten(text, cap)` 截断（cap 按列典型宽度定）；右对齐列（数字/状态）用 `rcell(text, width)` 前导空格补齐，**表头同样 rcell**。
- 列宽：`fit_table_columns(table, weights, rows=即将装载的行数)` 按权重瓜分容器宽（返回值含每列左右 2 空格 padding）。
- **列宽/表头/单元格必须同一帧同源生成**：垂直滚动条用行数确定性预判（`rows > region.height - 3` 预留 2 列），不要依赖 `max_scroll_y`（装载前后会变）；布局稳定后整表重建一次（`call_after_refresh`），勿只改列宽不重建单元格。
- 列宽在装载函数与 `on_resize` 里重算（`on_resize` 直接定义即可，`Screen` 没有可 `super()` 的默认实现）。
- `.tbl` 加 `overflow-x: hidden` 兜底防横向滚动条；`:focus` 态边框也必须保持圆角（`.tbl:focus { border: round $primary }`），否则聚焦时出现直角，与 `.panel` 风格割裂。

## 10. 浮层（页内弹层）三要素

- 自定义 `Message` 类要能被 `@on(..., "#id")` 匹配必须提供 `control` 属性（返回 `self._sender`）。
- 浮层父容器必须声明 `layers: base overlay`，否则鼠标命中下层组件。
- 浮层本体：`position: absolute; overlay: screen; layer: overlay; display` 切换；定位用 `styles.offset`；右缘控件的浮层右对齐展开并防越界。
- Button 内容宽 = 区域宽 − padding(2) − `line-pad`(1×2)；`line-pad: 0` **非法**（须 ≥1），紧凑网格按钮按 `文本宽 + 4` 定 `width`/`min-width` 并加宽容器。

## 11. 取数与并发逻辑

- 阻塞调用（云 SDK / requests / paramiko / subprocess）**一律** `asyncio.to_thread`，包在 `@work(exclusive=True)` worker 里，`on_mount` 触发。
- 实时流：`@work(thread=True)` + `call_from_thread` 回 UI 线程；`on_unmount` 置停止位并 close response，防线程泄漏。
- 轮询：`set_interval` + busy 标志去重；`on_unmount` 停 timer。
- 展示刷新函数与列宽计算解耦：`_rebuild/_load`（数据 → 单元格 + fit）与 worker（取数）分开。

## 12. Textual CSS / 8.2.8 通用陷阱

- 无 `:not()`、无 `z-index`、无 `top`/`left`（用 `offset` 样式属性）、无相邻兄弟选择器 `+`（解析直接报错，行距用显式类如 `.filter-row.gap-top`）。
- `max-height` 不接受 `none`，不限制就删声明。
- 8.2.8 无 `Spacer`（横向撑开用 `Static(classes="fill")` + `width: 1fr`）、无 `Spinner`、无 `.instant` 类。
- `cell_len` 在 `rich.cells`（复数模块名）。
- 自定义 `DataTable` 子类重写 `_on_click`：Textual 沿 MRO 逐类派发，接管与回落两条路径都要 `event.prevent_default()`，否则消息双发/双执行。
- 屏幕方法不要命名 `_render`（覆盖 `Widget._render` 渲染崩溃）。
- **级联优先级**：app.tcss（含 Screen CSS）对同一属性**始终压过** widget 自身 `DEFAULT_CSS`，与选择器特异度无关——给通用控件写全局规则前，先 grep 各控件 DEFAULT_CSS 里对该控件的 width/padding 定制（历史事故：全局 `Button{padding:0 1}` 掀翻 MonthPicker 3 格小按钮，rich `chop_cells` 除零崩溃）。
- `#topbar Button { margin }` 这类**后代选择器**会命中挂在顶栏里的组件内部按钮（MonthPicker 弹层等），且 id 前缀特异度压过 widget 类规则；顶栏间距规则必须限定到具体 id（`#topbar #top-back`）。
- `OptionList` 的 `DEFAULT_CSS` 自带 `border: tall $border-blurred`（直角框）。放进 `.panel` 圆角容器时必须显式 `border: none; background: transparent`（如 `#mh-list`），否则容器内套一个直角方框。
- 首页 Logo 每行**不要保留尾随空格**：`logo_text()` 对每行 `rstrip()`、`LOGO_WIDTH` 用 `cell_len(line.rstrip())`；否则 `text-align: center` 会按各行裁剪后的宽度独立居中，出现「某一行错位」。
- `textual.widgets` 各版本导出位置不同，`Option` 用 try/except import。
- `Select` 内部 `#label` 是 `width: 1fr`：放进 `Horizontal` 行且不给显式宽度时，auto 宽度会把整行剩余空间全吃掉，`▼` 箭头溢出容器被裁剪——行内 Select 必须显式定宽（如设置页模式 `width: 12`）。

## 13. 验证规范（三项目同一模板）

- `App.run_test(size=(120, 40))` 宽屏 + `(LOGO_WIDTH+6 以下, 28)` 窄屏各跑一遍。
- 配置目录指向 `tempfile.mkdtemp()`（写最小配置），服务层用 Fake 对象整体屏蔽网络。
- 必测清单：各页挂载无异常；`#page.virtual_size.height <= container_size.height`（单滚动条）；表格列宽和 ≈ `region.width - 2` 且无横向溢出；单击行执行且**不重复压栈**；`Ctrl+N/E/D/R`；模态紧凑度（min-height/单行输入/行距/Tab 轮切/validator 拦截）；折叠菜单浮层开合与外部点击；Esc 层级（模态 > 菜单 > 返回）；窄终端 logo `-sm` 降级与列宽重算。
- 断言 API 坑：DOMQuery 计数用 `len(query)`（无 `.count()`）；滚动上限 `max_scroll_y`（无 `scroll_max`）；Pilot 无 `shift_tab()`，用 `pilot.press("shift+tab")`；Fake 服务要**带状态**（setter 改 getter 返回值）。

## 14. 跨项目组件对照

| 组件 | aliyun-controller | clash-controller | litellm-controller |
|---|---|---|---|
| `ui.PageScreen` / `ui.LOGO_*` | ✅ | ✅ | ✅ |
| `ui.MonthPicker` / `ui.QuotaBar` | ✅（账期/额度场景） | ➖ 无场景未搬运 | ➖ 无场景未搬运 |
| `widgets.ClickTable` / `HintBar` / `MenuMarquee` / `shorten` / `rcell` / `fit_table_columns` | ✅ | ✅ | ✅ |
| `widgets` 四模态 | ✅（FormModal 无 textarea） | ✅（FormModal 多 `textarea` kind） | ✅（FormModal 多 `textarea` + `browse` 字段） |
| `PickModal` / `MultiPickModal` 选择弹窗 | ➖ | ➖ | ✅（litellm 特有：项目选择 / 模糊多选） |
| sheet 式大表单（ModalScreen） | ➖ | ➖（用 FormModal 足够） | ✅（ModelFormScreen / GroupFormScreen / ModelMetaFormScreen） |
| 模糊筛选 `filter_fuzzy` | ✅ | ✅ | ✅ |

---

# 第二部分 · 本项目（clash-controller）

## 项目概览

- 基于 Textual 的终端 TUI，管理 Clash 实例：端点档案、实时概览 / 连接、配置拉取与部署、设置与升级。
- 包名 `clash_controller`，命令 `clashctl`（也支持 `python -m clash_controller`）。
- 界面与分层风格与 `../aliyun-controller`、`../litellm-controller`、`../llm-price-compare` 保持一致。

## 开发命令

```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

./venv/bin/clashctl                  # 启动 TUI
./venv/bin/clashctl -D /path/dir     # 指定配置目录
./venv/bin/clashctl --debug          # API / SSH 调试日志（写入应用日志页）
./venv/bin/clashctl --version

ruff check clash_controller          # 改动后必须零告警
```

## 架构

```
clash_controller/
  main.py / __main__.py   入口：argparse(-D/--dir, --debug, --version) + App().run()
  app.py                  ClashControllerApp：档案/客户端缓存、状态栏、日志、report()
  config.py               profiles.json / config_providers.json 读写（纯数据）
  api.py                  ClashAPI（(result, error) 契约）+ interpret_api_result
  ssh.py                  SSH / 本地文件部署服务层
  deploy.py               provider 拉取、当前配置读取、更新决策
  ui.py                   界面骨架：PageScreen / logo 常量（8.x 特性集中在这里）
  widgets.py              模态 + 表格与模糊筛选辅助（比 aliyun 多 textarea 字段）
  app.tcss                全局样式（模态三段式、顶栏、kv-row、home banner、状态栏）
  screens/                profiles(根屏) / home / overview / config / settings / logs
```

分层与服务约定见第一部分 §2；本项目服务层特有：

- `api.py` 保留 `(result, error)` 二元返回契约；`error == ClashAPI.SENT_BUT_DISCONNECTED` 表示「请求已发送但读响应前断连」，需提示用户手动确认。
- `ssh.py` / `deploy.py` 失败抛 `RuntimeError`，由屏幕层 `notify_err`。
- 结果反馈统一 `app.report(action_desc, result, error)`（内部 `interpret_api_result` → status/notify/日志）。

## 本项目界面实例（对第一部分规范的映射）

- 根屏 `ProfileListScreen`：Screen 变体（居中 + CLASH 大 logo + 选项列表 OptionList，按数字 1-9 快捷连接，`[＋ 新增端点][编辑][删除]` 按钮居中；端点名 shorten 到 14 列、`URL · 类型` 描述右对齐超宽跑马灯；`Esc`/`Ctrl+C` 退出，不绑 `q`）。
- 首页：CLASH logo（`LOGO_WIDTH` 自动计算，窄于 `LOGO_WIDTH+6` 降级）+ 4 菜单项（概览/配置/设置/切换端点），描述右对齐、超宽跑马灯；数字 1-4、`Esc`/`Ctrl+C` 退出（切换端点走菜单项 `4`），不绑 `q`。
- 概览页：上 `.panel` 一组 kv 行（版本/流量/内存/连接），数据来自流式与每秒轮询；下接活动连接表 DataTable（每秒刷新）。
- 配置页：`.filter-row` [拉取并部署][＋ 新增][编辑][删除][重载配置] + ClickTable，**单击行 = 拉取比对部署**（后续 ConfirmModal 把关）；列 cap 名称 24 / url 100。
- 设置页：顶栏副标题为当前端点名称。首个 `.panel` 容器含 `TUN [Switch]` 与 `模式 [Select]` 即时控制项（无需保存）；第二个 `.panel`（核心操作 / 重启升级按钮组）与表格/内容同列；「查看应用日志」在右上角 ⋮ 折叠菜单。
- 日志页：`RichLog(classes="tbl")`，reload_page 全量重放 `app.app_logs`。
- 编辑端点：FormModal（url/密钥 password/名称/类型 choice/目录/两个 switch）；remote 追加 SSHFormModal（choice 认证方式 + 条件校验 + 私钥 textarea）。
- 文本编辑模式：`ui.PageScreen` 焦点在 Input/TextArea 时把 `#page-hint` 切为 `EDIT_HINT` 并禁用 `Ctrl+R`；`FormModal`/`InputModal` 底部 `#frm-hint`/`#im-hint` 按焦点类型切换 `HINT_FORM`/`HINT_FORM_EDIT`/`HINT_FORM_TA`（详见第一部分 §7.2）。

## 配置与文件

- 配置目录优先级：`-D/--dir` > 环境变量 `CLASH_CONTROLLER_CONFIG_DIR` > `~/.config/clash-controller`；`main.py` 在导入 `app` 前设置环境变量。
- `profiles.json`：端点档案列表。必填 `endpoint_type` / `config_directory` / `use_sudo`；`endpoint_type == "remote"` 必须带 `ssh`（见 `validate_profile`）。`ssh.auth_type` 为 `password` 或 `private_key`。
- `config_providers.json`：`[{"name": ..., "url": ...}]`。
- 无历史格式兼容（beta 阶段，旧格式会提示重新创建）。
- `--debug` 或档案 `debug_ssh` 的调试信息必须走 `add_log`，绝不 `print`。

## 新增功能指引

1. 服务逻辑写入 `api.py` / `deploy.py` / `ssh.py` 等顶层模块，保持 `(result, error)` 或抛异常。
2. 新页面直接继承 `ui.PageScreen`；在 `screens/home.py` 的 `ITEMS` / `_open()` 注册入口（同步数字快捷键）。
3. 需要确认的操作走 `ConfirmModal`；持久化统一走 `config.py` 的 load/save。
4. 样式复用 `app.tcss` 的 `.page-hint` / `.tbl` / `.panel` / `.filter-row` / `.kv-row` / `.modal-*` / `.btn-row`。

## 本项目陷阱与历史决定

- 概览屏刷新方法叫 `_refresh_panel`（`_render` 陷阱案例）；`App.debug` 只读，本项目用 `debug_mode`。
- 实时流 worker（`_consume`）务必保证退出路径：`_stop` 位 + `response.close()`，改动时防线程泄漏。
- `unix://` 端点支持在 `ClashAPI.__init__` 内处理（`removeprefix("unix://")` + URL 编码）。
- `InquirerPy` 已移除、未使用的 `scp` 已从依赖移除，均勿引入。
- 部署链路（fetch/apply）在测试中 monkeypatch `screens.config` 模块内的函数名，避免真实网络与 sudo。

## 依赖与质量

- `pyproject.toml`：`requests`、`requests_unixsocket`、`paramiko`、`textual>=8.0`。
- 依赖变更后同步更新 `requirements.txt`；改动入口点时 `pip install -e .` 刷新 console script。

## 验证（本项目脚本）

- `/tmp/opencode/clash_check.py`（纯逻辑 + 基础挂载）、`/tmp/opencode/clash_ui_check.py`（55 断言：根屏/单击连接与部署/Ctrl 键位/模态紧凑/switch/浮层菜单/Esc 层级/流式面板/窄终端 44 列），可作新脚本模板。
- 做法：`CLASH_CONTROLLER_CONFIG_DIR` 指向临时目录并写 `profiles.json` / `config_providers.json`；`app.get_api = lambda: FakeAPI()` 屏蔽网络；FakeAPI 要带状态（`toggle_tun`/`set_mode` 改变 `get_configs` 返回值）。

## 提交

- 不要执行 `git commit` 等写操作，由用户自行提交。

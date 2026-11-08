"""节点屏：浏览策略组与成员、单节点 / 整组测速、手动指定与恢复自动。

延迟来自服务端 ``history`` / ``extra``；测速结果只写回内存快照，下一次 ``_load()``
以服务端数据为准。URLTest / Fallback 的「固定」选择由内核持久化，整组测速会清除它，
所以发起前必须确认。
"""
from __future__ import annotations

import asyncio
import time

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.dom import NoScreen
from textual.widgets import Button, DataTable, Input, Select, Static

from ..api import (
    DELAY_OK,
    DELAY_TIMEOUT,
    interpret_delay_result,
    interpret_group_delay_result,
)
from ..proxies import (
    DEFAULT_TEST_TIMEOUT,
    GROUP_TYPE_LABELS,
    ProxiesSnapshot,
    ProxyGroup,
    ProxyNode,
    build_snapshot,
    group_test_url,
)
from ..ui import PageScreen
from ..widgets import (
    STYLE_DIM,
    STYLE_ERR,
    STYLE_OK,
    STYLE_WARN,
    ConfirmModal,
    fit_table_columns,
    hold_busy,
    load_rows,
    make_table,
    shorten,
)

COLUMN_WEIGHTS = [5.0, 1.6, 0.8, 1.4, 2.0]

SORT_OPTIONS = [
    ("延迟升序", "delay_asc"),
    ("延迟降序", "delay_desc"),
    ("名称", "name"),
    ("原始顺序", "natural"),
]


def _attached(widget) -> bool:
    """控件链是否仍连着屏幕（页面退出后写控件会抛 NoScreen）。"""
    try:
        return widget.screen is not None
    except NoScreen:
        return False


class NodeScreen(PageScreen):
    TITLE = "节点"
    HINT = (
        "↑↓ 移动 · Tab 切换控件 · 回车/双击 指定 · Ctrl+T 测节点 · Ctrl+G 测整组 · "
        "Ctrl+U 恢复自动 · / 搜索 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"
    )

    BINDINGS = [
        Binding("ctrl+t", "test_node", show=False),
        Binding("ctrl+g", "test_group", show=False),
        Binding("ctrl+u", "unfix_group", show=False),
        Binding("/", "focus_filter", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._snapshot: ProxiesSnapshot | None = None
        self._group = ""
        self._filter_text = ""
        self._sort_key = "delay_asc"
        self._show_hidden = False
        self._rows: list[str] = []
        self._testing: set[str] = set()
        self._fail_reason: dict[str, str] = {}
        self._nested_delay: dict[str, int] = {}
        self._group_testing = False
        self._syncing = False
        self.menu = [(self._hidden_label, self._toggle_hidden, False)]

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="filter-row"):
                yield Static("策略组", classes="fl-label")
                yield Select(
                    [("加载中…", "")],
                    value="",
                    allow_blank=False,
                    compact=True,
                    id="nd-group",
                )
                yield Static("排序", classes="fl-label")
                yield Select(
                    [(label, key) for label, key in SORT_OPTIONS],
                    value=self._sort_key,
                    allow_blank=False,
                    compact=True,
                    id="nd-sort",
                )
                yield Static("筛选", classes="fl-label")
                yield Input(placeholder="节点名称", compact=True, id="nd-filter")
            with Horizontal(classes="filter-row gap-top"):
                yield Button("测节点", id="nd-test-node")
                yield Button("测整组", id="nd-test-group")
                yield Button("指定", id="nd-select", variant="primary")
                yield Button("恢复自动", id="nd-unfix")
                yield Static("", classes="fill")
                yield Static("", id="nd-stats", classes="fl-label")
        # 延迟列用 justify="right" 让 Rich 按实际列宽对齐（含 CJK 宽度），
        # 不要用 str.rjust：它按字符数补空格，中文标签会撑破列宽被裁掉。
        yield make_table(
            "名称", "类型", "UDP", Text("延迟", justify="right"), "来源", id="nd-table"
        )

    def on_mount(self) -> None:
        self.set_subtitle((self.app.profile or {}).get("name", ""))
        self._load()
        self.query_one("#nd-table", DataTable).focus()

    def on_resize(self, event) -> None:
        self._refit()

    def reload_page(self) -> None:
        self._load()

    # ------------------------------------------------------------ 数据加载

    @work(exclusive=True, group="nodes-load")
    async def _load(self) -> None:
        table = self.query_one("#nd-table", DataTable)
        started = time.monotonic()
        if _attached(table):
            table.loading = True
        try:
            try:
                api = self.app.get_api()
            except Exception as e:  # noqa: BLE001
                self.app.notify_err(f"获取节点失败: {type(e).__name__}: {e}")
                return

            async def call(fn):
                try:
                    return await asyncio.to_thread(fn)
                except Exception as e:  # noqa: BLE001
                    return None, f"{type(e).__name__}: {e}"

            (proxies_result, error), (providers_result, _) = await asyncio.gather(
                call(api.get_proxies), call(api.get_proxy_providers)
            )
            if error:
                self.app.notify_err(f"获取节点失败: {error}")
                return

            self._snapshot = build_snapshot(proxies_result, providers_result)
            self._nested_delay.clear()
            if not _attached(self):
                return
            self._apply_snapshot()
        finally:
            try:
                if self.is_mounted:
                    await hold_busy(started)
            finally:
                if _attached(table):
                    table.loading = False
                    self.call_after_refresh(self._refit)

    def _apply_snapshot(self) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        groups = snapshot.group_list(self._show_hidden)
        names = [group.name for group in groups]
        select = self.query_one("#nd-group", Select)
        self._syncing = True
        try:
            if names:
                select.set_options([(self._group_prompt(group), group.name) for group in groups])
                if self._group not in names:
                    self._group = names[0]
                select.value = self._group
            else:
                select.set_options([("（无策略组）", "")])
                self._group = ""
        finally:
            self._syncing = False
        select.disabled = not names
        self._render_nodes()
        if not names:
            self.app.status("未获取到策略组", "warn")
        else:
            group = self._current_group()
            count = len(group.all) if group is not None else 0
            self.app.status(f"共 {len(names)} 个策略组 · 当前 {count} 个成员")

    @staticmethod
    def _group_prompt(group: ProxyGroup) -> str:
        label = GROUP_TYPE_LABELS.get(group.kind) or group.type or "未知"
        return f"{shorten(group.name, 18)} · {label}"

    def _current_group(self) -> ProxyGroup | None:
        if self._snapshot is None or not self._group:
            return None
        return self._snapshot.groups.get(self._group)

    def _member(self, group: ProxyGroup, name: str) -> ProxyNode:
        snapshot = self._snapshot
        if snapshot is None:
            return ProxyNode(name=name, type="")
        node = snapshot.member(group, name)
        if node.is_group and name in self._nested_delay:
            node.delay = self._nested_delay[name]
        return node

    # ------------------------------------------------------------ 渲染

    def _refit(self) -> None:
        try:
            table = self.query_one("#nd-table", DataTable)
        except Exception:  # noqa: BLE001 — 组合早期
            return
        fit_table_columns(table, COLUMN_WEIGHTS, rows=len(self._rows))

    def _render_nodes(self) -> None:
        table = self.query_one("#nd-table", DataTable)
        group = self._current_group()
        # load_rows → DataTable.clear() 会把光标与滚动位置归零；测速 / 指定后按
        # 成员名把光标放回原行，否则连续按 Ctrl+T 会一直测第一行。
        old_name = self._selected_name()
        old_scroll = table.scroll_y
        self._rows = self._sorted_members(group) if group is not None else []
        rows = [self._row_cells(group, name) for name in self._rows] if group is not None else []
        load_rows(table, rows)
        if old_name is not None and old_name in self._rows:
            table.cursor_coordinate = (self._rows.index(old_name), 0)
        if table.row_count:
            table.scroll_y = min(old_scroll, table.max_scroll_y)
        self._refit()
        self._update_stats()
        self._update_subtitle()
        self._sync_actions()

    def _sorted_members(self, group: ProxyGroup) -> list[str]:
        needle = self._filter_text.strip().casefold()
        names = [name for name in group.all if not needle or needle in name.casefold()]
        key = self._sort_key
        if key == "natural":
            return names
        if key == "name":
            return sorted(names, key=str.casefold)
        descending = key == "delay_desc"

        def sort_key(name: str):
            delay = self._member(group, name).delay
            # None / 0 无论升降序都沉底，同值按名称稳定排列
            if delay is None or delay <= 0:
                return (1, 0, name.casefold())
            return (0, -delay if descending else delay, name.casefold())

        return sorted(names, key=sort_key)

    def _row_cells(self, group: ProxyGroup, name: str) -> list[Text]:
        node = self._member(group, name)
        current = name == group.now
        name_text = Text("✓ " if current else "  ", style=STYLE_OK)
        name_text.append(shorten(name, 40), style="bold" if current else "")
        if node.is_group:
            source = "组"
        elif node.provider:
            source = node.provider
        elif node.type:
            source = "配置"
        else:
            source = "—"
        return [
            name_text,
            Text(shorten(node.type or "—", 16)),
            Text("✓" if node.udp else ""),
            self._delay_cell(name, node),
            Text(shorten(source, 20)),
        ]

    def _delay_cell(self, name: str, node: ProxyNode) -> Text:
        if name in self._testing:
            return Text("测试中", justify="right", style=STYLE_DIM)
        delay = node.delay
        if delay is None:
            return Text("未测", justify="right", style=STYLE_DIM)
        if delay <= 0:
            reason = self._fail_reason.get(name, "失败")
            return Text(reason, justify="right", style=STYLE_ERR)
        if delay <= 200:
            style = STYLE_OK
        elif delay <= 500:
            style = STYLE_WARN
        else:
            style = STYLE_ERR
        return Text(f"{delay}ms", justify="right", style=style)

    def _update_stats(self) -> None:
        group = self._current_group()
        if group is None:
            self.query_one("#nd-stats", Static).update(Text(""))
            return
        available = failed = untested = 0
        for name in group.all:
            delay = self._member(group, name).delay
            if delay is None:
                untested += 1
            elif delay <= 0:
                failed += 1
            else:
                available += 1
        text = f"共 {len(group.all)} · 可用 {available} · 失败 {failed} · 未测 {untested}"
        self.query_one("#nd-stats", Static).update(Text(text))

    def _update_subtitle(self) -> None:
        group = self._current_group()
        if group is None:
            self.set_subtitle((self.app.profile or {}).get("name", ""))
            return
        kind = GROUP_TYPE_LABELS.get(group.kind) or group.type or "未知"
        text = f"{shorten(group.name, 18)} · {kind} · 当前 {shorten(group.now or '—', 18)}"
        if group.fixed:
            text += f" · 固定 {shorten(group.fixed, 14)}"
        self.set_subtitle(text)

    def _sync_actions(self) -> None:
        group = self._current_group()
        has_rows = bool(self._rows)
        can_select = group is not None and group.kind != "loadbalance" and has_rows
        can_unfix = group is not None and group.kind in ("urltest", "fallback") and bool(group.fixed)
        self.query_one("#nd-select", Button).disabled = not can_select
        self.query_one("#nd-unfix", Button).disabled = not can_unfix
        self.query_one("#nd-test-node", Button).disabled = not has_rows
        self.query_one("#nd-test-group", Button).disabled = not (
            group is not None and bool(group.all)
        ) or self._group_testing

    def _hidden_label(self) -> str:
        return "不显示隐藏组" if self._show_hidden else "显示隐藏组"

    def _toggle_hidden(self) -> None:
        self._show_hidden = not self._show_hidden
        self._apply_snapshot()

    # ------------------------------------------------------------ 筛选控件

    @on(Input.Changed, "#nd-filter")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        self._filter_text = event.value
        self._render_nodes()

    @on(Select.Changed, "#nd-group")
    def _on_group_changed(self, event: Select.Changed) -> None:
        if self._syncing or event.value is Select.NULL:
            return
        self._group = str(event.value)
        self._render_nodes()

    @on(Select.Changed, "#nd-sort")
    def _on_sort_changed(self, event: Select.Changed) -> None:
        if event.value is Select.NULL:
            return
        self._sort_key = str(event.value)
        self._render_nodes()

    @on(DataTable.RowHighlighted, "#nd-table")
    def _on_row_highlighted(self) -> None:
        self._update_stats()
        self._sync_actions()

    def action_focus_filter(self) -> None:
        self.query_one("#nd-filter", Input).focus()

    def _selected_name(self) -> str | None:
        index = self.query_one("#nd-table", DataTable).cursor_row
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    # ------------------------------------------------------------ 手动指定 / 恢复自动

    @on(Button.Pressed, "#nd-select")
    def _on_select_button(self) -> None:
        self._assign_selected()

    @on(DataTable.RowSelected, "#nd-table")
    def _on_row_selected(self) -> None:
        self._assign_selected()

    def _assign_selected(self) -> None:
        group = self._current_group()
        name = self._selected_name()
        if group is None or name is None:
            self.app.notify_warn("请先选择一个节点")
            return
        if group.kind == "loadbalance":
            self.app.notify_warn("负载均衡组不支持手动指定")
            return
        self._assign(group.name, name)

    @work(exclusive=True, group="nodes-assign")
    async def _assign(self, group_name: str, name: str) -> None:
        self.app.spinner(True, "正在切换…")
        try:
            api = self.app.get_api()
            result, error = await asyncio.to_thread(api.select_proxy_in_group, group_name, name)
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"切换节点失败: {type(e).__name__}: {e}")
            return
        finally:
            self.app.spinner(False)
        severity = self.app.report("切换节点", result, error)
        if not _attached(self) or severity != "ok" or self._snapshot is None:
            return
        group = self._snapshot.groups.get(group_name)
        if group is None:
            return
        group.now = name
        if group.kind in ("urltest", "fallback"):
            group.fixed = name
        self._render_nodes()

    def action_unfix_group(self) -> None:
        group = self._current_group()
        if group is None:
            self.app.notify_warn("请先选择策略组")
            return
        if group.kind not in ("urltest", "fallback"):
            self.app.notify_warn("只有自动（URLTest）/ 故障转移组可以恢复自动")
            return
        if not group.fixed:
            self.app.notify_warn("当前策略组没有固定选择，无需恢复自动")
            return
        self._unfix(group.name)

    @on(Button.Pressed, "#nd-unfix")
    def _on_unfix_button(self) -> None:
        self.action_unfix_group()

    @work(exclusive=True, group="nodes-unfix")
    async def _unfix(self, group_name: str) -> None:
        self.app.spinner(True, "正在恢复自动…")
        try:
            api = self.app.get_api()
            result, error = await asyncio.to_thread(api.reset_auto_group_selection, group_name)
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"恢复自动失败: {type(e).__name__}: {e}")
            return
        finally:
            self.app.spinner(False)
        self.app.report("恢复自动", result, error)
        if error is None and _attached(self):
            self._load()

    # ------------------------------------------------------------ 单节点测速

    @on(Button.Pressed, "#nd-test-node")
    def _on_test_node_button(self) -> None:
        self.action_test_node()

    def action_test_node(self) -> None:
        group = self._current_group()
        name = self._selected_name()
        if group is None or name is None:
            self.app.notify_warn("请先选择一个节点")
            return
        if name in self._testing:
            return
        node = self._member(group, name)
        self._testing.add(name)
        self._render_nodes()
        self._run_node_test(group_test_url(group), name, node.provider if not node.is_group else "")

    @work(exclusive=False, group="node-test")
    async def _run_node_test(self, url: str, name: str, provider: str) -> None:
        try:
            api = self.app.get_api()
            if provider:
                result, error = await asyncio.to_thread(
                    api.test_provider_proxy_delay, provider, name, url, DEFAULT_TEST_TIMEOUT
                )
            else:
                result, error = await asyncio.to_thread(
                    api.test_proxy_delay, name, url, DEFAULT_TEST_TIMEOUT
                )
        except Exception as e:  # noqa: BLE001
            result, error = None, f"{type(e).__name__}: {e}"
        status, delay = interpret_delay_result(result, error)
        self._testing.discard(name)
        if not _attached(self):
            return
        snapshot = self._snapshot
        if snapshot is None:
            self._render_nodes()
            return
        node = snapshot.nodes.get(name)
        if node is not None:
            node.delay = delay if status == DELAY_OK and delay is not None else 0
        elif status == DELAY_OK and delay is not None:
            self._nested_delay[name] = delay
        else:
            self._nested_delay.pop(name, None)

        if status == DELAY_OK and delay is not None:
            self._fail_reason.pop(name, None)
            self.app.status(f"{shorten(name, 24)} 延迟 {delay}ms", "ok")
        elif status == DELAY_TIMEOUT:
            self._fail_reason[name] = "超时"
            self.app.status(f"{shorten(name, 24)} 测速超时", "err")
        else:
            self._fail_reason[name] = "失败"
            self.app.status(f"{shorten(name, 24)} 测速失败", "err")
        self._render_nodes()

    # ------------------------------------------------------------ 整组测速

    @on(Button.Pressed, "#nd-test-group")
    def _on_test_group_button(self) -> None:
        self.action_test_group()

    def action_test_group(self) -> None:
        group = self._current_group()
        if group is None or not group.all:
            self.app.notify_warn("当前策略组没有可测速的节点")
            return
        if self._group_testing:
            return
        if group.kind in ("urltest", "fallback") and group.fixed:
            def confirm(ok: bool) -> None:
                if ok:
                    self._run_group_test(group.name)

            self.app.push_screen(
                ConfirmModal(
                    f"测整组会清除该组的固定选择（当前固定：{shorten(group.fixed, 18)}），继续吗？",
                    title="测整组",
                    yes="继续",
                    default_yes=False,
                ),
                confirm,
            )
            return
        self._run_group_test(group.name)

    @work(exclusive=False, group="group-test")
    async def _run_group_test(self, group_name: str) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        group = snapshot.groups.get(group_name)
        if group is None:
            return
        self._group_testing = True
        self._sync_actions()
        self.app.spinner(True, f"正在测速 {shorten(group.name, 18)}…")
        status = ""
        delays: dict[str, int] = {}
        error = None
        try:
            try:
                api = self.app.get_api()
                result, error = await asyncio.to_thread(
                    api.test_group_delay, group.name, group_test_url(group), DEFAULT_TEST_TIMEOUT
                )
            except Exception as e:  # noqa: BLE001
                result, error = None, f"{type(e).__name__}: {e}"
            status, delays = interpret_group_delay_result(result, error)
        finally:
            self.app.spinner(False)
            self._group_testing = False
            if _attached(self):
                self._sync_actions()

        if not _attached(self):
            return
        if status == DELAY_OK:
            for name in group.all:
                node = snapshot.nodes.get(name)
                if node is None:
                    continue
                delay = delays.get(name, 0)
                node.delay = delay
                if delay <= 0:
                    self._fail_reason[name] = "失败"
            self.app.status(f"{shorten(group.name, 18)} 测速完成：{len(delays)} 个可用", "ok")
        elif status == DELAY_TIMEOUT:
            for name in group.all:
                node = snapshot.nodes.get(name)
                if node is None:
                    continue
                node.delay = 0
                self._fail_reason[name] = "超时"
            self.app.status(f"{shorten(group.name, 18)} 整组测速超时", "err")
        else:
            detail = error
            if not detail and isinstance(result, dict):
                detail = result.get("message") or result.get("body")
            self.app.notify_err(f"整组测速失败：{shorten(str(detail or '内核返回错误'), 60)}")
            return
        self._render_nodes()
        self._load()

"""概览屏：版本 + 实时流量 / 内存（流式接口）+ 当前活动连接表（每秒轮询）。

连接表支持筛选（关键字 / 节点 / 隐藏 DIRECT）与排序（速度 / 时长 / 文本），
速度由相邻两次轮询的累计字节差值得出（与 YACD / Metacubexd 的做法一致）。
"""
import asyncio
import json
import time
from collections.abc import Callable
from datetime import datetime

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.dom import NoScreen
from textual.widgets import Button, DataTable, Input, Select, Static, Switch

from ..ui import PageScreen
from ..widgets import fit_table_columns, hold_busy, load_rows, make_table, shorten

COLUMN_WEIGHTS = [3.2, 1.0, 2.5, 2.0, 1.2, 1.2, 1.0]

SORT_OPTIONS = [
    ("默认", "natural"),
    ("下载速度", "down_speed"),
    ("上传速度", "up_speed"),
    ("下载总量", "download"),
    ("上传总量", "upload"),
    ("连接时长", "duration"),
    ("地址", "host"),
    ("规则", "rule"),
    ("节点", "chain"),
]
# 这些排序键的自然方向是「大在前」（速度、流量、时长）
DESC_SORTS = {"down_speed", "up_speed", "download", "upload", "duration"}
NODE_ALL = "__all__"


def _attached(widget) -> bool:
    """控件链是否仍连着屏幕。

    页面退出时 worker 会被取消，而父链可能先于 `is_mounted` 复位被摘掉，
    这时写 `table.loading` 会抛 NoScreen（覆盖层本来也随页面一起消失了）。
    """
    try:
        return widget.screen is not None
    except NoScreen:
        return False


def _fmt_bytes(value, unit_suffix: str = "") -> str:
    """把字节数压成人类可读单位（B / KB / MB / GB / TB）。"""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            text = f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            return text + unit_suffix
        n /= 1024
    return f"{n:.1f} TB{unit_suffix}"


def _fmt_speed(value) -> str:
    return "—" if value is None else _fmt_bytes(value, "/s")


def _fmt_rate(value) -> str:
    """表格内的紧凑速率（列宽有限）：1.2M / 880K / 12B，单位由表头声明。"""
    if value is None:
        return "—"
    n = float(value)
    if n < 1024:
        return f"{n:.0f}B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f}K" if n < 10 * 1024 else f"{n / 1024:.0f}K"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f}M"
    return f"{n / 1024 ** 3:.1f}G"


def _fmt_duration(seconds) -> str:
    """紧凑时长（列宽有限）：45s / 12m / 3h / 2d。"""
    if seconds is None:
        return "—"
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def _parse_time(value) -> datetime | None:
    """解析 Clash 的 RFC3339 时间；兼容 3.10（Z 后缀、纳秒小数位）。"""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    if "." in text:
        head, rest = text.split(".", 1)
        split = len(rest)
        for i, ch in enumerate(rest):
            if ch in "+-":
                split = i
                break
        text = f"{head}.{rest[:split][:6]}{rest[split:]}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _elapsed_seconds(start) -> float | None:
    parsed = _parse_time(start)
    if parsed is None:
        return None
    now = datetime.now(tz=parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return max(0.0, (now - parsed).total_seconds())


def _host_text(metadata: dict) -> str:
    host = metadata.get("host") or metadata.get("destinationIP") or "未知"
    port = metadata.get("destinationPort")
    if port and not str(host).endswith(f":{port}"):
        return f"{host}:{port}"
    return str(host)


def _is_direct(chains) -> bool:
    return any(str(name).upper() == "DIRECT" for name in (chains or []))


def _chain_text(chains) -> str:
    return " → ".join(str(name) for name in (chains or []))


class OverviewScreen(PageScreen):
    TITLE = "概览"
    HINT = (
        "↑↓ / PgUp / PgDn 滚动 · / 搜索 · Tab 切换筛选控件 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"
    )

    BINDINGS = [Binding("/", "focus_filter", "搜索", show=False)]

    def __init__(self) -> None:
        super().__init__()
        self._stop = False
        self._responses: list = []
        self._traffic = {"up": 0, "down": 0}
        self._memory = {"inuse": 0}
        self._version = "获取中…"
        self._timer = None
        self._busy = False
        self._count = 0
        # 连接筛选 / 排序状态
        self._connections: list[dict] = []
        self._row_ids: list[str] = []
        self._prev: dict[str, tuple[float, float, float]] = {}
        self._speeds: dict[str, tuple[float, float]] = {}
        self._stats = {"count": 0, "download": 0, "upload": 0, "down_speed": 0.0, "up_speed": 0.0}
        self._filter_text = ""
        self._node_filter = NODE_ALL
        self._hide_direct = False
        self._sort_key = "natural"
        self._desc = False
        self._node_names: list[str] = []
        self._syncing = False

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="kv-row"):
                yield Static("版本", classes="kv-label")
                yield Static(self._version, classes="kv-value", id="ov-version")
            with Horizontal(classes="kv-row"):
                yield Static("流量", classes="kv-label")
                yield Static("", classes="kv-value", id="ov-traffic")
            with Horizontal(classes="kv-row"):
                yield Static("内存", classes="kv-label")
                yield Static("", classes="kv-value", id="ov-memory")
            with Horizontal(classes="kv-row"):
                yield Static("连接", classes="kv-label")
                yield Static("", classes="kv-value", id="cn-summary")
            with Horizontal(classes="filter-row gap-top"):
                yield Static("筛选", classes="fl-label")
                yield Input(placeholder="地址 / 规则 / 节点 / 进程", compact=True, id="cn-filter")
                yield Static("节点", classes="fl-label")
                yield Select(
                    [("全部节点", NODE_ALL)],
                    value=NODE_ALL,
                    allow_blank=False,
                    compact=True,
                    id="cn-node",
                )
            with Horizontal(classes="filter-row gap-top"):
                yield Static("排序", classes="fl-label")
                yield Select(
                    [(label, key) for label, key in SORT_OPTIONS],
                    value=self._sort_key,
                    allow_blank=False,
                    compact=True,
                    id="cn-sort",
                )
                yield Button("↓ 降序", id="cn-order")
                yield Static("", classes="fill")
                yield Static("隐藏 DIRECT", classes="fl-label")
                yield Switch(id="cn-nodirect")
        yield make_table(
            "地址", "网络", "节点", "规则", "↓速度", "↑速度", "时长", id="cn-table", cursor="none"
        )

    def on_mount(self) -> None:
        self.set_subtitle((self.app.profile or {}).get("name", ""))
        self._refresh_panel()
        self._fetch_version()
        api = self.app.get_api()
        self._consume("流量", api.get_traffic_stream, self._on_traffic)
        self._consume("内存", api.get_memory_stream, self._on_memory)
        self.query_one(DataTable).focus()
        self._sync_order_button()
        self._timer = self.set_interval(1.0, self._refresh_connections)
        self._refresh_connections(show_overlay=True)

    def on_unmount(self) -> None:
        self._stop = True
        if self._timer is not None:
            self._timer.stop()
        for response in self._responses:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

    def on_resize(self, event) -> None:
        self._refit()

    def reload_page(self) -> None:
        self._fetch_version()
        self._refresh_connections(show_overlay=True)

    # ------------------------------------------------------------ 后台线程

    def _safe_call(self, callback: Callable, *args) -> None:
        try:
            self.app.call_from_thread(callback, *args)
        except Exception:  # noqa: BLE001 — App 可能已退出
            pass

    @work(exclusive=True, thread=True)
    def _fetch_version(self) -> None:
        try:
            version_info, error = self.app.get_api().get_version()
        except Exception as e:  # noqa: BLE001
            version_info, error = None, str(e)
        version = version_info.get("version", "未知") if version_info else f"N/A ({error})"
        self._safe_call(self._set_version, version)

    @work(thread=True)
    def _consume(self, label: str, method: Callable, handler: Callable) -> None:
        try:
            response, error = method()
        except Exception as e:  # noqa: BLE001
            response, error = None, str(e)
        if error or response is None:
            self._safe_call(self._stream_lost, label, error)
            return

        self._responses.append(response)
        try:
            for line in response.iter_lines():
                if self._stop:
                    break
                if not line:
                    continue
                text = line.decode("utf-8")
                if text.startswith("data: "):
                    text = text[len("data: "):]
                try:
                    payload = json.loads(text)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                self._safe_call(handler, payload)
        except Exception as e:  # noqa: BLE001
            self._safe_call(self._stream_lost, label, str(e))
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ 连接轮询

    def _refit(self) -> None:
        fit_table_columns(self.query_one("#cn-table", DataTable), COLUMN_WEIGHTS, rows=self._count)

    @work(exclusive=False)
    async def _refresh_connections(self, show_overlay: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        started = time.monotonic()
        table = self.query_one("#cn-table", DataTable)
        if show_overlay:
            table.loading = True
        try:
            try:
                data, error = await asyncio.to_thread(self.app.get_api().get_connections)
            except Exception as e:  # noqa: BLE001
                self.app.notify_err(f"获取连接失败: {type(e).__name__}: {e}")
                return
            if not self.is_mounted:
                return
            if error:
                self.app.notify_err(f"获取连接失败: {error}")
                return

            data = data if isinstance(data, dict) else {}
            connections = data.get("connections", []) or []

            # 速度 = 相邻两轮采样累计字节差 / 时间差；新连接首轮没有样本，显示 —
            now = time.monotonic()
            speeds: dict[str, tuple[float, float]] = {}
            prev: dict[str, tuple[float, float, float]] = {}
            for conn in connections:
                cid = str(conn.get("id", ""))
                down = float(conn.get("download", 0) or 0)
                up = float(conn.get("upload", 0) or 0)
                old = self._prev.get(cid)
                if old is not None:
                    dt = now - old[2]
                    if dt > 0:
                        speeds[cid] = (max(0.0, (down - old[0]) / dt), max(0.0, (up - old[1]) / dt))
                prev[cid] = (down, up, now)
            self._prev = prev
            self._speeds = speeds
            self._connections = connections
            self._stats = {
                "count": len(connections),
                "download": float(data.get("downloadTotal", 0) or 0),
                "upload": float(data.get("uploadTotal", 0) or 0),
                "down_speed": sum(s[0] for s in speeds.values()),
                "up_speed": sum(s[1] for s in speeds.values()),
            }
            self._sync_node_options(connections)
            self._render_rows()
        finally:
            self._busy = False
            if show_overlay:
                # 无论正常/异常/取消，覆盖层都必须撤下
                try:
                    if self.is_mounted:
                        await hold_busy(started)
                finally:
                    if _attached(table):
                        table.loading = False
                if _attached(table):
                    self.call_after_refresh(self._refit)  # 覆盖层撤下后区域宽可能变化，按最终宽度重排
                    table.focus()

    # ------------------------------------------------------------ 筛选 / 排序

    def _sync_node_options(self, connections: list[dict]) -> None:
        """把连接里出现过的节点同步为下拉选项；节点集合不变时不动控件。"""
        names = sorted(
            {str(name) for conn in connections for name in (conn.get("chains") or [])},
            key=str.casefold,
        )
        if names == self._node_names:
            return
        self._node_names = names
        select = self.query_one("#cn-node", Select)
        self._syncing = True
        try:
            select.set_options([("全部节点", NODE_ALL), *[(shorten(n, 30), n) for n in names]])
            if self._node_filter not in names:
                self._node_filter = NODE_ALL
            select.value = self._node_filter
        finally:
            self._syncing = False

    def _sort_value(self, conn: dict, key: str):
        metadata = conn.get("metadata", {}) or {}
        if key == "down_speed":
            return self._speeds.get(str(conn.get("id", "")), (-1.0, -1.0))[0]
        if key == "up_speed":
            return self._speeds.get(str(conn.get("id", "")), (-1.0, -1.0))[1]
        if key == "download":
            return float(conn.get("download", 0) or 0)
        if key == "upload":
            return float(conn.get("upload", 0) or 0)
        if key == "duration":
            seconds = _elapsed_seconds(conn.get("start"))
            return -1.0 if seconds is None else seconds
        if key == "rule":
            return f"{conn.get('rule', '?')}|{conn.get('rulePayload', '')}".casefold()
        if key == "chain":
            return _chain_text(conn.get("chains")).casefold()
        return _host_text(metadata).casefold()

    def _render_rows(self) -> None:
        """按当前筛选条件与排序键重建行；尽量保持光标所在连接与滚动位置。"""
        table = self.query_one("#cn-table", DataTable)
        needle = self._filter_text.strip().casefold()

        view = []
        for conn in self._connections:
            metadata = conn.get("metadata", {}) or {}
            chains = conn.get("chains") or []
            if self._hide_direct and _is_direct(chains):
                continue
            if self._node_filter != NODE_ALL and self._node_filter not in chains:
                continue
            if needle:
                haystack = " ".join(
                    [
                        _host_text(metadata),
                        str(metadata.get("destinationIP", "")),
                        str(metadata.get("network", "")),
                        str(conn.get("rule", "")),
                        str(conn.get("rulePayload", "")),
                        _chain_text(chains),
                        str(metadata.get("processPath") or metadata.get("process") or ""),
                    ]
                ).casefold()
                if needle not in haystack:
                    continue
            view.append(conn)

        # 「默认」保持 API 原始顺序；其余排序先按地址定次序再按主键排，
        # 避免同值时每秒跳来跳去。
        if self._sort_key != "natural":
            view.sort(key=lambda c: _host_text(c.get("metadata", {}) or {}).casefold())
            view.sort(key=lambda c: self._sort_value(c, self._sort_key), reverse=self._desc)

        old_id = None
        cursor = table.cursor_row
        if 0 <= cursor < len(self._row_ids):
            old_id = self._row_ids[cursor]
        scroll = table.scroll_y

        rows = []
        ids = []
        for conn in view:
            metadata = conn.get("metadata", {}) or {}
            chains = conn.get("chains") or []
            cid = str(conn.get("id", ""))
            down_speed, up_speed = self._speeds.get(cid, (None, None))
            rows.append(
                [
                    Text(shorten(_host_text(metadata), 60)),
                    Text(str(metadata.get("network", "?"))),
                    Text(shorten(_chain_text(chains), 32)),
                    Text(shorten(str(conn.get("rule", "?")), 24)),
                    Text(_fmt_rate(down_speed)),
                    Text(_fmt_rate(up_speed)),
                    Text(_fmt_duration(_elapsed_seconds(conn.get("start")))),
                ]
            )
            ids.append(cid)

        self._row_ids = ids
        self._count = len(rows)
        load_rows(table, rows)
        self._refit()
        self._restore_position(table, old_id, scroll)
        self._update_summary(len(view))

    def _restore_position(self, table: DataTable, old_id: str | None, scroll: int) -> None:
        if old_id is not None and old_id in self._row_ids and table.row_count:
            table.cursor_coordinate = (self._row_ids.index(old_id), 0)
        elif table.row_count and table.cursor_row >= table.row_count:
            table.cursor_coordinate = (table.row_count - 1, 0)
        table.scroll_y = min(scroll, table.max_scroll_y)

    def _update_summary(self, shown: int) -> None:
        stats = self._stats
        total = stats["count"]
        parts = [f"共 {total} 条"]
        if shown != total:
            parts.append(f"显示 {shown} 条")
        parts.append(
            f"↓ {_fmt_speed(stats['down_speed'])} ↑ {_fmt_speed(stats['up_speed'])}"
        )
        parts.append(
            f"累计 ↓ {_fmt_bytes(stats['download'])} ↑ {_fmt_bytes(stats['upload'])}"
        )
        self.query_one("#cn-summary", Static).update(Text(" · ".join(parts)))
        self.app.status(f"连接 {total} 条，显示 {shown} 条")

    # ------------------------------------------------------------ 筛选控件事件

    @on(Input.Changed, "#cn-filter")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        self._filter_text = event.value
        self._render_rows()

    @on(Select.Changed, "#cn-node")
    def _on_node_changed(self, event: Select.Changed) -> None:
        if self._syncing or event.value is Select.NULL:
            return
        self._node_filter = str(event.value)
        self._render_rows()

    @on(Select.Changed, "#cn-sort")
    def _on_sort_changed(self, event: Select.Changed) -> None:
        if event.value is Select.NULL:
            return
        self._sort_key = str(event.value)
        self._desc = self._sort_key in DESC_SORTS  # 换排序键时回到该键的自然方向
        self._sync_order_button()
        self._render_rows()

    @on(Button.Pressed, "#cn-order")
    def _on_order_pressed(self) -> None:
        if self._sort_key == "natural":
            return
        self._desc = not self._desc
        self._sync_order_button()
        self._render_rows()

    def _sync_order_button(self) -> None:
        button = self.query_one("#cn-order", Button)
        # 「默认」是 API 原始顺序，没有升/降可言
        button.disabled = self._sort_key == "natural"
        button.label = "↓ 降序" if self._desc else "↑ 升序"

    @on(Switch.Changed, "#cn-nodirect")
    def _on_hide_direct(self, event: Switch.Changed) -> None:
        self._hide_direct = bool(event.value)
        self._render_rows()

    def action_focus_filter(self) -> None:
        self.query_one("#cn-filter", Input).focus()

    # ------------------------------------------------------------ UI 更新

    def _set_version(self, version: str) -> None:
        self._version = version
        self._refresh_panel()

    def _on_traffic(self, payload: dict) -> None:
        self._traffic = payload or {"up": 0, "down": 0}
        self._refresh_panel()

    def _on_memory(self, payload: dict) -> None:
        self._memory = payload or {"inuse": 0}
        self._refresh_panel()

    def _stream_lost(self, label: str, error) -> None:
        self.app.add_log(f"{label} 实时流中断: {error}")
        if self.is_mounted and not self._stop:
            self.app.notify_warn(f"{label} 实时流已中断: {error}")

    def _refresh_panel(self) -> None:
        if not self.is_mounted:
            return
        up = self._traffic.get("up", 0) / 1024
        down = self._traffic.get("down", 0) / (1024)
        mem = self._memory.get("inuse", 0) / (1024 * 1024)
        self.query_one("#ov-version", Static).update(self._version)
        self.query_one("#ov-traffic", Static).update(f"↑ {up:.2f} KB/s    ↓ {down:.2f} KB/s")
        self.query_one("#ov-memory", Static).update(f"{mem:.2f} MB")

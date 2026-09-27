"""概览屏：版本 + 实时流量 / 内存（流式接口）+ 当前活动连接表（每秒轮询）。"""
import asyncio
import json
import time
from collections.abc import Callable
from datetime import datetime

from rich.text import Text
from textual import work
from textual.containers import Horizontal, Vertical
from textual.dom import NoScreen
from textual.widgets import DataTable, Static

from ..ui import PageScreen
from ..widgets import fit_table_columns, hold_busy, load_rows, make_table, shorten

COLUMN_WEIGHTS = [4, 1.5, 1.5, 3, 3]


def _attached(widget) -> bool:
    """控件链是否仍连着屏幕。

    页面退出时 worker 会被取消，而父链可能先于 `is_mounted` 复位被摘掉，
    这时写 `table.loading` 会抛 NoScreen（覆盖层本来也随页面一起消失了）。
    """
    try:
        return widget.screen is not None
    except NoScreen:
        return False


class OverviewScreen(PageScreen):
    TITLE = "概览"
    HINT = "↑↓ / PgUp / PgDn 滚动连接表 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

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
        yield make_table("地址", "网络", "类型", "规则", "代理链", id="cn-table", cursor="none")

    def on_mount(self) -> None:
        self.set_subtitle((self.app.profile or {}).get("name", ""))
        self._refresh_panel()
        self._fetch_version()
        api = self.app.get_api()
        self._consume("流量", api.get_traffic_stream, self._on_traffic)
        self._consume("内存", api.get_memory_stream, self._on_memory)
        self.query_one(DataTable).focus()
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
            total_dl = data.get("downloadTotal", 0) / (1024 * 1024)
            total_ul = data.get("uploadTotal", 0) / (1024 * 1024)

            rows = []
            for conn in connections:
                metadata = conn.get("metadata", {}) or {}
                host = metadata.get("host") or metadata.get("destinationIP", "未知")
                rows.append([
                    Text(shorten(str(host), 40)),
                    Text(str(metadata.get("network", "?"))),
                    Text(str(metadata.get("type", "?"))),
                    Text(shorten(str(conn.get("rule", "?")), 30)),
                    Text(shorten(" -> ".join(conn.get("chains", []) or []), 30)),
                ])
            self._count = len(rows)
            load_rows(table, rows)
            self._refit()
            self.query_one("#cn-summary", Static).update(
                Text(
                    f"{len(connections)} 条 · 上传 {total_ul:.2f} MB / 下载 {total_dl:.2f} MB"
                    f" · 更新于 {datetime.now():%H:%M:%S}"
                )
            )
            self.app.status(f"连接总数 {len(connections)}")
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

"""应用日志屏：滚动展示本次运行积累的日志。"""
from textual.widgets import RichLog

from ..ui import PageScreen


class LogScreen(PageScreen):
    TITLE = "应用日志"
    HINT = "↑↓ / PgUp / PgDn 滚动 · Ctrl+R 重载 · Esc/Ctrl+C 返回"

    def compose_page(self):
        yield RichLog(id="log-view", markup=False, wrap=True, classes="tbl")

    def on_mount(self) -> None:
        self._load()
        self.query_one("#log-view", RichLog).focus()

    def reload_page(self) -> None:
        self._load()

    def _load(self) -> None:
        log = self.query_one("#log-view", RichLog)
        log.clear()
        if self.app.app_logs:
            for line in self.app.app_logs:
                log.write(line)
            log.scroll_end(animate=False)
        else:
            log.write("暂无日志。")

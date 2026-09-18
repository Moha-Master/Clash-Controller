"""配置管理屏：配置提供方的增删改，以及拉取 / 比对 / 部署 / 重载流程。"""
import asyncio

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Static

from ..config import load_config_providers, save_config_providers
from ..deploy import compute_sha256, evaluate_update, fetch_remote_config, read_current_config
from ..ssh import apply_local_config_file, apply_remote_config_via_ssh
from ..ui import PageScreen
from ..widgets import ConfirmModal, FormField, FormModal, fit_table_columns, load_rows, make_table, shorten

COLUMN_WEIGHTS = [2, 7]


class ConfigScreen(PageScreen):
    TITLE = "配置管理"
    HINT = "↑↓ 移动 · 回车 / 单击 拉取并部署所选 · Ctrl+N 新增 · Ctrl+E 编辑 · Ctrl+D 删除 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

    BINDINGS = [
        Binding("ctrl+n", "add_provider", show=False),
        Binding("ctrl+e", "edit_provider", show=False),
        Binding("ctrl+d", "delete_provider", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._providers: list = []
        self._display: list = []

    def compose_page(self):
        with Horizontal(classes="filter-row"):
            yield Button("拉取并部署", id="deploy", variant="primary", compact=True)
            yield Button("＋ 新增", id="add", compact=True)
            yield Button("编辑", id="edit", compact=True)
            yield Button("删除", id="delete", variant="error", compact=True)
            yield Button("重载配置", id="reload-api", compact=True)
            yield Static("", classes="fill")
            yield Static("单击行 = 部署该提供方", classes="fl-label")
        yield make_table("名称", "地址", id="cf-table")

    def on_mount(self) -> None:
        self.set_subtitle((self.app.profile or {}).get("name", ""))
        self._load()
        self.query_one(DataTable).focus()
        self.call_after_refresh(self._refit)

    def on_resize(self, event) -> None:
        self._refit()

    def reload_page(self) -> None:
        self._load()

    def _refit(self) -> None:
        fit_table_columns(self.query_one("#cf-table", DataTable), COLUMN_WEIGHTS, rows=len(self._display))

    def _load(self) -> None:
        self._providers = load_config_providers(self.app.add_log)
        self._display = list(self._providers)
        rows = [[Text(shorten(p.get("name", "?"), 24)), Text(shorten(p.get("url", ""), 100))] for p in self._providers]
        load_rows(self.query_one("#cf-table", DataTable), rows)
        self.set_subtitle(f"{len(self._providers)} 个提供方")
        self.app.status(f"共 {len(self._providers)} 个配置提供方" if self._providers else "暂无配置提供方")

    def _selected(self) -> dict | None:
        idx = self.query_one("#cf-table", DataTable).cursor_row
        if 0 <= idx < len(self._display):
            return self._display[idx]
        return None

    # ------------------------------------------------------------ 提供方增删改

    def action_add_provider(self) -> None:
        self._edit_provider(None)

    def action_edit_provider(self) -> None:
        provider = self._selected()
        if provider is None:
            self.app.notify_warn("请先选择一个提供方")
            return
        self._edit_provider(provider)

    def action_delete_provider(self) -> None:
        provider = self._selected()
        if provider is None:
            self.app.notify_warn("请先选择一个提供方")
            return

        def confirm(ok: bool) -> None:
            if not ok:
                return
            try:
                self._providers.remove(provider)
            except ValueError:
                return
            save_config_providers(self._providers)
            self.app.notify_ok("已删除配置提供方")
            self._load()

        self.app.push_screen(
            ConfirmModal(
                f"确定要删除提供方“{provider.get('name', '?')}”吗？",
                title="删除提供方",
                yes="删除",
                default_yes=False,
            ),
            confirm,
        )

    @on(Button.Pressed, "#add")
    def _on_add(self) -> None:
        self.action_add_provider()

    @on(Button.Pressed, "#edit")
    def _on_edit(self) -> None:
        self.action_edit_provider()

    @on(Button.Pressed, "#delete")
    def _on_delete(self) -> None:
        self.action_delete_provider()

    def _edit_provider(self, existing: dict | None) -> None:
        existing = existing or {}
        fields = [
            FormField(
                "url", "更新链接", value=existing.get("url", ""),
                validator=lambda v: v.startswith(("http://", "https://")),
                error="需为 http(s) 链接",
                hint="例如 http://example.com/config.yaml",
            ),
            FormField(
                "name", "提供方名称", value=existing.get("name", existing.get("url", "")),
                placeholder="留空则使用链接",
            ),
        ]

        def done(data: dict | None) -> None:
            if data is None:
                return
            name = data["name"].strip() or data["url"].strip()
            entry = {"name": name, "url": data["url"].strip()}
            if existing:
                idx = self._providers.index(existing)
                self._providers[idx] = entry
            else:
                self._providers.append(entry)
            save_config_providers(self._providers)
            self.app.notify_ok("已保存配置提供方")
            self._load()

        self.app.push_screen(FormModal("编辑配置提供方" if existing else "添加配置提供方", fields), done)

    # ------------------------------------------------------------ 拉取并部署

    @on(DataTable.RowSelected, "#cf-table")
    def _on_row(self, event: DataTable.RowSelected) -> None:
        provider = self._selected()
        if provider:
            self._deploy(provider)

    @on(Button.Pressed, "#deploy")
    def _on_deploy_button(self) -> None:
        provider = self._selected()
        if provider is None:
            self.app.notify_warn("请先选择一个提供方")
            return
        self._deploy(provider)

    @work(exclusive=True)
    async def _deploy(self, provider: dict) -> None:
        profile = self.app.profile or {}
        debug = self.app.debug_mode or bool(profile.get("debug_ssh"))
        url = provider.get("url", "")

        self.app.status(f"正在从 {url} 拉取配置…")
        self.app.add_log(f"Fetching config from {url}...")
        try:
            remote_payload, remote_hash, remote_mod_time = await asyncio.to_thread(fetch_remote_config, url)
            local_mtime, local_bytes = await asyncio.to_thread(
                read_current_config, profile, self.app.add_log, debug
            )
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"拉取或读取配置失败: {type(e).__name__}: {e}")
            return

        local_hash = compute_sha256(local_bytes)
        decision = evaluate_update(remote_hash, remote_mod_time, local_hash, local_mtime)

        proceed = True
        if decision == "auto":
            self.app.add_log("Remote config newer with different hash. Proceeding without prompt.")
        elif decision == "confirm_older":
            proceed = await self.app.push_screen_wait(
                ConfirmModal("远程配置可能比本地更旧，仍要覆盖目标配置吗？", default_yes=False)
            )
        elif decision == "confirm_unknown":
            proceed = await self.app.push_screen_wait(
                ConfirmModal("远程配置内容不同，但无法获取更新时间。是否继续覆盖目标配置？", default_yes=True)
            )
        else:  # confirm_same
            proceed = await self.app.push_screen_wait(
                ConfirmModal("远程配置与目标配置内容相同，仍要覆盖吗？", default_yes=False)
            )

        if not proceed:
            self.app.status("已取消更新", "warn")
            self.app.add_log("Config update cancelled by user after comparison.")
            return

        self.app.status("正在部署配置…")
        try:
            if profile.get("endpoint_type") == "remote":
                self.app.add_log("Deploying config to remote endpoint via SSH.")
                await asyncio.to_thread(
                    apply_remote_config_via_ssh, profile, remote_payload, self.app.add_log, debug
                )
            else:
                self.app.add_log("Deploying config to local endpoint file.")
                await asyncio.to_thread(apply_local_config_file, profile, remote_payload)
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"部署配置失败: {type(e).__name__}: {e}")
            return

        self.app.status("正在通过 Clash API 重载配置…")
        try:
            result, error = await asyncio.to_thread(self.app.get_api().reload_configs)
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"重载配置失败: {type(e).__name__}: {e}")
            return
        self.app.report("重载配置", result, error)

    @on(Button.Pressed, "#reload-api")
    def _on_reload_api(self) -> None:
        self._reload_api()

    @work(exclusive=True)
    async def _reload_api(self) -> None:
        self.app.status("正在重载配置…")
        try:
            result, error = await asyncio.to_thread(self.app.get_api().reload_configs)
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"重载配置失败: {type(e).__name__}: {e}")
            return
        self.app.report("重载配置", result, error)

"""端点档案屏（根屏）：居中 LOGO 版式，选择 / 连接 / 新增 / 编辑 / 删除（含 SSH 子表单）。"""
import asyncio

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, OptionList, Static

from .. import __version__
from ..config import load_profiles, save_profiles
from ..deploy import default_endpoint_type
from ..ui import LOGO_WIDTH, home_col_width, logo_text
from ..widgets import ConfirmModal, FormField, FormModal, HintBar, MenuMarquee

try:  # textual 各版本 Option 导出位置不同
    from textual.widgets.option_list import Option
except ImportError:  # pragma: no cover
    from textual.widgets._option_list import Option

REQUIRED_FIELDS = ["endpoint_type", "config_directory", "use_sudo"]


def validate_profile(profile: dict) -> str | None:
    """校验档案字段完整性，合法返回 None。"""
    if any(k not in profile for k in REQUIRED_FIELDS):
        return "该配置使用旧版本格式，请重新创建"
    if profile.get("endpoint_type") == "remote" and not profile.get("ssh"):
        return "远程配置缺少 SSH 设置，请重新创建"
    return None


def build_profile(data: dict, ssh: dict | None = None) -> dict:
    return {
        "name": data["name"].strip(),
        "url": data["url"].strip(),
        "secret": data.get("secret", ""),
        "endpoint_type": data["endpoint_type"],
        "config_directory": data["config_directory"].strip() or "/etc/clash",
        "use_sudo": bool(data["use_sudo"]),
        "debug_ssh": bool(data["debug_ssh"]),
        "ssh": ssh,
    }


def _auth_value(existing: dict) -> str:
    if existing.get("auth_type") == "private_key":
        return "key_file" if existing.get("private_key_path") else "key_text"
    return existing.get("auth_type", "password") or "password"


def build_ssh(data: dict) -> dict:
    auth = data["auth_type"]
    ssh = {
        "host": data["host"].strip(),
        "port": int(data["port"]),
        "username": data["username"].strip() or "root",
        "ignore_hostkey": bool(data.get("ignore_hostkey")),
        "auth_type": "password" if auth == "password" else "private_key",
    }
    if auth == "password":
        ssh["password"] = data["password"]
    elif auth == "key_file":
        ssh["private_key_path"] = data["private_key_path"].strip()
        ssh["passphrase"] = data["passphrase"]
    else:
        ssh["private_key"] = data["private_key"]
        ssh["passphrase"] = data["passphrase"]
    return ssh


def _basic_fields(existing: dict | None) -> list:
    existing = existing or {}
    url = existing.get("url", "")
    return [
        FormField(
            "url", "控制端点", value=url,
            validator=lambda v: v.startswith(("http://", "https://", "unix://")),
            error="需为 http(s):// 或 unix://",
            hint="例如 http://127.0.0.1:9090 或 unix:///tmp/clash.sock",
        ),
        FormField(
            "secret", "API 密钥", kind="password", value=existing.get("secret", ""),
            hint="留空保持原密钥",
        ),
        FormField(
            "name", "端点名称", value=existing.get("name", url),
            validator=lambda v: bool(v.strip()), error="不能为空",
        ),
        FormField(
            "endpoint_type", "端点类型", kind="choice",
            value=existing.get("endpoint_type", ""),
            options=[("本地", "local"), ("远程", "remote")],
            hint="留空将根据端点地址自动判断",
        ),
        FormField(
            "config_directory", "配置目录",
            value=existing.get("config_directory", "/etc/clash"),
            validator=lambda v: bool(v.strip()), error="不能为空",
            hint="Clash 核心的配置目录（config.yaml 所在目录）",
        ),
        FormField(
            "use_sudo", "使用 sudo", kind="switch",
            value="on" if existing.get("use_sudo", True) else "",
        ),
        FormField(
            "debug_ssh", "SSH 调试日志", kind="switch",
            value="on" if existing.get("debug_ssh") else "",
        ),
    ]


class ProfileBasicsModal(FormModal):
    """端点基础表单：未显式选择类型时，根据 URL 自动判定。"""

    def _collect(self) -> "dict | None":
        data = super()._collect()
        if data is None:
            return None
        if not data.get("endpoint_type"):
            data["endpoint_type"] = default_endpoint_type(data["url"])
        return data


class SSHFormModal(FormModal):
    """SSH 连接参数表单，按认证方式条件校验。"""

    def __init__(self, existing: dict | None = None) -> None:
        existing = existing or {}
        fields = [
            FormField(
                "host", "SSH 地址", value=existing.get("host", ""),
                validator=lambda v: bool(v.strip()), error="不能为空",
            ),
            FormField(
                "port", "SSH 端口", value=str(existing.get("port", 22)),
                validator=lambda v: bool(v.strip()), error="不能为空",
            ),
            FormField(
                "username", "用户名", value=existing.get("username", "root"),
                validator=lambda v: bool(v.strip()), error="不能为空",
            ),
            FormField(
                "auth_type", "认证方式", kind="choice", value=_auth_value(existing),
                options=[("密码", "password"), ("私钥（文件）", "key_file"), ("私钥（文本）", "key_text")],
            ),
            FormField(
                "password", "SSH 密码", kind="password", value=existing.get("password", ""),
                hint="留空保持原密码",
            ),
            FormField("private_key_path", "私钥文件路径", value=existing.get("private_key_path", "")),
            FormField(
                "passphrase", "私钥口令", kind="password", value=existing.get("passphrase", ""),
                hint="可选；留空保持不变",
            ),
            FormField("private_key", "私钥文本", kind="textarea", value=existing.get("private_key", "")),
            FormField(
                "ignore_hostkey", "忽略 HostKey", kind="switch",
                value="on" if existing.get("ignore_hostkey") else "",
            ),
        ]
        super().__init__("SSH 设置", fields)

    def _collect(self) -> "dict | None":
        data = super()._collect()
        if data is None:
            return None
        try:
            port = int(str(data["port"]).strip())
        except (TypeError, ValueError):
            self.show_error("SSH 端口必须是整数")
            return None
        if not (1 <= port <= 65535):
            self.show_error("SSH 端口必须在 1-65535 之间")
            return None
        data["port"] = port

        auth = data["auth_type"]
        if auth == "password" and not data["password"]:
            self.show_error("密码认证需要填写 SSH 密码")
            return None
        if auth == "key_file" and not data["private_key_path"].strip():
            self.show_error("私钥（文件）认证需要填写私钥文件路径")
            return None
        if auth == "key_text" and not data["private_key"].strip():
            self.show_error("私钥（文本）认证需要粘贴私钥内容")
            return None
        return data


class ProfileListScreen(Screen):
    """启动后的根屏幕：与首页同款居中 LOGO 版式，管理并连接 Clash 端点。"""

    CSS_CLASSES = "logo-page"

    HINT = "↑↓ 选择 · 回车 / 单击 连接 · 1-9 直达 · Ctrl+N 新增 · Ctrl+E 编辑 · Ctrl+D 删除 · Ctrl+R 刷新 · Esc/Ctrl+C 退出"

    BINDINGS = [
        Binding("escape,ctrl+c", "quit_app", "退出"),
        Binding("ctrl+n", "add_profile", show=False),
        Binding("ctrl+e", "edit_profile", show=False),
        Binding("ctrl+d", "delete_profile", show=False),
        Binding("ctrl+r", "refresh_page", show=False),
    ] + [Binding(str(i), f"pick({i - 1})", show=False) for i in range(1, 10)]

    def __init__(self) -> None:
        super().__init__(classes="logo-page")
        self._profiles: list = []
        self._display: list = []
        self._marquee = None

    # ------------------------------------------------------------ 组合

    def compose(self):
        with Vertical(id="home-main"):
            with Vertical(id="home-col"):
                yield Static(logo_text(), id="home-banner")
                yield Static(Text("Clash Controller", style="bold"), id="home-plain")
                yield Static(f"v{__version__}", id="home-version")
                yield OptionList(id="pf-list")
                with Horizontal(id="pf-actions"):
                    yield Button("＋ 新增端点", id="add", variant="primary")
                    yield Button("编辑", id="edit")
                    yield Button("删除", id="delete", variant="error")
        yield HintBar(self.HINT, classes="page-hint")

    def on_mount(self) -> None:
        self._marquee = MenuMarquee(self, "pf-list", [])
        self._load()
        self.query_one("#pf-list", OptionList).focus()
        self._apply_breakpoint()
        self.call_after_refresh(self._marquee.start)

    def on_resize(self, event) -> None:
        self._apply_breakpoint()
        if self.is_mounted:
            self._rebuild()

    def _apply_breakpoint(self) -> None:
        """宽度不足以容纳艺术字时降级为普通标题文本；列宽跟随 logo。"""
        small = self.size.width < LOGO_WIDTH + 6
        self.set_class(small, "-sm")
        self.query_one("#home-col").styles.width = None if small else home_col_width()

    # ------------------------------------------------------------ 数据加载

    def _load(self) -> None:
        self._profiles = load_profiles(self.app.add_log)
        self._rebuild()

    def reload_page(self) -> None:
        self._load()

    def action_refresh_page(self) -> None:
        self._load()

    def _rows(self) -> list[tuple[str, str]]:
        labels = {"local": "本地", "remote": "远程"}
        rows = []
        for p in self._display:
            kind = labels.get(p.get("endpoint_type", ""), p.get("endpoint_type", "?"))
            name = str(p.get("name", "?") or "?")
            rows.append((name, f"{p.get('url', '')} · {kind}"))
        return rows

    def _rebuild(self) -> None:
        if self._marquee is None:
            return
        self._display = list(self._profiles)
        ol = self.query_one("#pf-list", OptionList)
        prev = ol.highlighted
        ol.clear_options()
        if self._display:
            self._marquee.rows = self._rows()
            ol.add_options(self._marquee.placeholder_options())
            self._marquee.set_rows(self._rows())
        else:
            ol.add_options([Option(Text("（暂无端点，Ctrl+N 创建）", style="dim"), disabled=True)])
        ol.highlighted = prev if prev is not None and 0 <= prev < len(self._display) else (0 if self._display else None)
        n = len(self._profiles)
        self.query_one("#home-version", Static).update(f"v{__version__} · {n} 个端点")
        if n:
            self.app.status(f"共 {n} 个端点，回车连接")
        else:
            self.app.status("暂无端点配置，Ctrl+N 创建", "warn")

    def _highlighted(self) -> dict | None:
        idx = self.query_one("#pf-list", OptionList).highlighted
        if idx is not None and 0 <= idx < len(self._display):
            return self._display[idx]
        return None

    # ------------------------------------------------------------ 连接

    @on(OptionList.OptionSelected, "#pf-list")
    def _on_row(self, event: OptionList.OptionSelected) -> None:
        if 0 <= event.option_index < len(self._display):
            self._connect(self._display[event.option_index])

    def action_pick(self, index: int) -> None:
        if 0 <= index < len(self._display):
            self._connect(self._display[index])

    @work(exclusive=True)
    async def _connect(self, profile: dict) -> None:
        error = validate_profile(profile)
        if error:
            self.app.notify_err(f"{profile.get('name', '?')}: {error}")
            return
        self.app.set_profile(profile)
        self.app.add_log(f"Selected profile '{profile.get('name')}'.")
        self.app.status(f"正在连接 {profile.get('url')} …")
        try:
            version_info, error = await asyncio.to_thread(self.app.get_api().get_version)
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"连接失败: {type(e).__name__}: {e}")
            self.app.clear_profile()
            return
        if version_info:
            version = version_info.get("version", "unknown")
            self.app.add_log(f"Successfully connected to Clash (version: {version}).")
            from .home import HomeScreen

            self.app.push_screen(HomeScreen())
        else:
            self.app.notify_err(f"连接失败，请检查端点配置并确认 Clash 正在运行。错误：{error}")
            self.app.clear_profile()

    # ------------------------------------------------------------ 增删改

    def action_add_profile(self) -> None:
        self._edit_profile(None)

    def action_edit_profile(self) -> None:
        profile = self._highlighted()
        if profile is None:
            self.app.notify_warn("请先选择一个端点")
            return
        self._edit_profile(profile)

    def action_delete_profile(self) -> None:
        profile = self._highlighted()
        if profile is None:
            self.app.notify_warn("请先选择一个端点")
            return

        def confirm(ok: bool) -> None:
            if not ok:
                return
            try:
                self._profiles.remove(profile)
            except ValueError:
                return
            save_profiles(self._profiles)
            self.app.notify_ok("已删除端点配置")
            self._load()

        self.app.push_screen(
            ConfirmModal(
                f"确定要删除端点“{profile.get('name', '?')}”吗？",
                title="删除端点",
                yes="删除",
                default_yes=False,
            ),
            confirm,
        )

    @on(Button.Pressed, "#add")
    def _on_add(self) -> None:
        self._edit_profile(None)

    @on(Button.Pressed, "#edit")
    def _on_edit(self) -> None:
        self.action_edit_profile()

    @on(Button.Pressed, "#delete")
    def _on_delete(self) -> None:
        self.action_delete_profile()

    @work(exclusive=True)
    async def _edit_profile(self, existing: dict | None) -> None:
        data = await self.app.push_screen_wait(
            ProfileBasicsModal("编辑端点" if existing else "新增端点", _basic_fields(existing))
        )
        if data is None:
            return

        ssh = existing.get("ssh") if existing else None
        if data["endpoint_type"] == "remote":
            ssh_data = await self.app.push_screen_wait(SSHFormModal((existing or {}).get("ssh")))
            if ssh_data is None:
                return
            ssh = build_ssh(ssh_data)

        profile = build_profile(data, ssh)
        if existing is None:
            self._profiles.append(profile)
        else:
            idx = self._profiles.index(existing)
            self._profiles[idx] = profile
        save_profiles(self._profiles)
        self.app.add_log(f"Saved profile '{profile['name']}'.")
        self.app.notify_ok("已保存端点配置")
        self._load()

    # ------------------------------------------------------------ 退出

    def action_quit_app(self) -> None:
        self.app.exit()

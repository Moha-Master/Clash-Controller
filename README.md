# Clash Controller

一个基于 [Textual](https://github.com/Textualize/textual) 构建的、功能丰富的 `clash` 文本用户界面（TUI）控制器，方便在终端中管理和监控一个或多个 `clash` 实例。

## 功能特性

- **现代 TUI 界面**：统一页面骨架（顶栏返回 / 折叠菜单 / 底部提示栏 + 状态栏），全键盘 + 全鼠标操作，单击数据行即执行，非阻塞异步加载与应用内日志。
- **多端点管理**：
    - 自动保存连接过的 Clash 端点（地址与密钥）。
    - 启动时进入类似首页的居中大 LOGO 档案选择页，支持新增 / 编辑 / 删除。
    - 支持 HTTP 与 Unix Domain Socket 连接。
    - 远程端点支持 SSH 部署（密码 / 私钥文件 / 私钥文本三种认证）。
- **实时监控面板**：
    - **概览**：合并流式监控与连接管理，在一个页面同时显示上/下行流量、内存使用、内核版本，并带每秒自动刷新的当前活动连接表与累计流量。
- **配置管理**：维护配置提供方列表，一键拉取远端配置、比对时间戳/哈希、部署到目标端点（本地或 SSH）并重载。
- **设置菜单**：端点名称置于顶栏，TUN 与规则/全局/直连模式在面板内直接通过 Switch 和 Select 下拉列表即时生效切换（无需手动保存）、重载 GEO、重启 Clash、在线升级内核 / UI / GEO，以及查看运行日志。

## 界面与快捷键

| 场景 | 快捷键 | 作用 |
|---|---|---|
| **端点选择（根屏）** | `↑` / `↓` · `回车` 或 **单击** | 移动光标 · 连接所选端点 |
| | `1-9` | 按数字直达连接第 N 个端点 |
| | `Ctrl+N` / `Ctrl+E` / `Ctrl+D` | 新增 / 编辑 / 删除端点配置 |
| | `Ctrl+R` | 刷新列表 |
| | `Esc` / `Ctrl+C` | 退出程序 |
| **主菜单** | `1` / `2` / `3` / `4` | 直达概览 / 配置管理 / 设置 / 切换端点 |
| | `↑` / `↓` · `回车` | 移动光标 · 确认 |
| | `Esc` / `Ctrl+C` | 退出程序（切换端点请按 `4`） |
| **配置管理** | `↑` / `↓` · `回车` 或 **单击行** | 移动光标 · 部署所选提供方 |
| | `Ctrl+N` / `Ctrl+E` / `Ctrl+D` | 新增 / 编辑 / 删除提供方 |
| | `Ctrl+R` | 刷新 |
| **功能页通用** | `Home` / `End` / `PgUp` / `PgDn` | 页面 / 表格滚动 |
| | `Tab` / `Shift+Tab` | 表单字段轮切焦点 |
| | `Esc` / `Ctrl+C` | 返回上一页 |
| | `Ctrl+Q` | 任意界面直接退出程序 |
| **弹窗** | `回车` | 提交（表单）/ 确认（危险操作需点按「确认」） |
| | `Esc` / `Ctrl+C` / 「取消」 | 取消并关闭 |

> 表单类弹窗统一为三段式：标题栏 / 字段区（单行紧凑输入，密码框留空 = 保持原值，开关为单行 Switch）/ 底部按钮栏 `[取消] [保存] [危险操作置右]`。
> 焦点进入输入框即切换为**文本编辑模式**：使用控件原生编辑键（`Home`/`End`、`Ctrl+Shift+A` 全选、`Ctrl+X/C/V` 剪切 / 复制 / 粘贴，`TextArea` 另有 `Ctrl+Z/Y` 撤销 / 重做），底部提示栏同步切换，此时 `Ctrl+R` 刷新让位给输入框；`Ctrl+C` 有选中则复制、无选中则返回 / 取消。
> 「查看应用日志」位于设置页右上角 `菜单 ▾` 折叠浮层中。

## 安装

```bash
pip install clash-controller
```

安装后，可以通过以下命令启动程序：

```bash
clashctl
```

> 需要 Python 3.10 及以上版本。

可选参数：

```bash
clashctl -D /path/to/config/dir   # 指定配置目录（默认 ~/.config/clash-controller）
clashctl --debug                  # 启用 API / SSH 调试日志（显示在应用日志页）
```

## 开发者安装

```bash
git clone https://github.com/Moha-Master/clash-controller.git
cd clash-controller

python -m venv venv
source venv/bin/activate  # 在 Windows 上使用 venv\Scripts\activate

pip install -e ".[dev]"

python -m clash_controller
```

## 要求

- Python 3.10+
- 运行中的 Clash 实例，已开启外部控制 API

## 许可证

MIT

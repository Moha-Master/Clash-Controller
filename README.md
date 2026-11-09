# Clash Controller

一个基于 [Textual](https://github.com/Textualize/textual) 构建的、功能丰富的 `clash` 文本用户界面（TUI）控制器，方便在终端中管理和监控一个或多个 `clash` 实例。

## 功能特性

- **现代 TUI 界面**：统一页面骨架（顶栏返回 / 折叠菜单 / 底部提示栏 + 状态栏），全键盘 + 全鼠标操作，数据行单击选中、双击执行，非阻塞异步加载与应用内日志。
- **多端点管理**：
    - 自动保存连接过的 Clash 端点（地址与密钥）。
    - 启动时进入类似首页的居中大 LOGO 档案选择页，支持新增 / 编辑 / 删除。
    - 支持 HTTP 与 Unix Domain Socket 连接。
    - 远程端点支持 SSH 部署（密码 / 私钥文件 / 私钥文本三种认证）。
- **实时监控面板**：
    - **概览**：合并流式监控与连接管理，在一个页面同时显示上/下行流量、内存使用、内核版本，并带每秒自动刷新的当前活动连接表与累计流量；连接表支持关键字（地址 / 规则 / 节点 / 进程）、节点、隐藏 DIRECT 筛选，以及按默认（API 原始顺序）、上/下载速度、流量、时长、地址、规则、节点排序（可切换升/降序）。
- **节点管理**：按策略组浏览组内成员（节点 / 嵌套组 / DIRECT / REJECT），显示服务端记录的最新延迟、失败与未测状态；支持单节点测速、整组测速、在组内手动指定当前节点，以及对 URLTest / 故障转移组恢复自动（清除固定选择）。provider 节点测速自动走 provider 作用域接口。
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
| **主菜单** | `1` / `2` / `3` / `4` / `5` | 直达概览 / 节点 / 配置管理 / 设置 / 切换端点 |
| | `↑` / `↓` · `回车` | 移动光标 · 确认 |
| | `Esc` / `Ctrl+C` | 退出程序（切换端点请按 `5`） |
| **概览** | `↑` / `↓` / `PgUp` / `PgDn` | 滚动连接表 |
| | `/` | 聚焦筛选输入框（地址 / 规则 / 节点 / 进程） |
| | `Tab` / `Shift+Tab` | 在筛选框、节点 / 排序下拉、隐藏 DIRECT 开关间轮切 |
| | `Ctrl+R` | 刷新（焦点在输入框时让位给文本编辑） |
| **节点** | `↑` / `↓` · `回车` 或 **双击行** | 移动光标 · 在组内指定所选节点 |
| | `Ctrl+T` | 测所选节点延迟 |
| | `Ctrl+G` | 测整组延迟（URLTest / 故障转移组会清除固定选择，先确认） |
| | `Ctrl+U` | 恢复自动（清除 URLTest / 故障转移组的固定选择） |
| | `/` | 聚焦节点名称筛选框 |
| | `Ctrl+R` | 刷新策略组与延迟 |
| **配置管理** | `↑` / `↓` · `回车` 或 **双击行** | 移动光标 · 部署所选提供方 |
| | `Ctrl+N` / `Ctrl+E` / `Ctrl+D` | 新增 / 编辑 / 删除提供方 |
| | `Ctrl+R` | 刷新 |
| **功能页通用** | `Home` / `End` / `PgUp` / `PgDn` | 页面 / 表格滚动 |
| | `Tab` / `Shift+Tab` | 表单字段轮切焦点 |
| | `Esc` / `Ctrl+C` | 返回上一页 |
| | `Ctrl+Q` | 任意界面直接退出程序 |
| **弹窗** | `回车` | 提交（表单）/ 确认（危险操作需点按「确认」） |
| | `Esc` / `Ctrl+C` / 「取消」 | 取消并关闭 |

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

## Windows 说明

- Clash 配置目录默认从 `%USERPROFILE%\.config\clash` / `%USERPROFILE%\.config\mihomo` 探测，也可在端点里手动填写。
- 若配置目录位于 `Program Files`、`ProgramData` 等受保护位置，部署时会弹出 UAC 请求管理员写入；请在端点设置中保持“管理员写入 (UAC)”开启。
- 订阅与本地配置会按 UTF-8 / GB18030 / 带 BOM 的 UTF-16 自动识别，并统一以 UTF-8（无 BOM、LF 换行）写入，不会再把中文变成乱码；控制 API 响应体同样按字节解码，中文节点名不受 `Content-Type` 缺 charset 影响。

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

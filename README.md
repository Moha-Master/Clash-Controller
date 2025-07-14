# Mihomo Controller

一个使用 `InquirerPy` 构建的、功能丰富的 `mihomo` 文本用户界面（TUI）控制器。它可以让您方便地通过命令行管理和监控一个或多个 `mihomo` 实例。

## 功能特性

- **交互式 TUI 界面**: 友好的菜单驱动操作，无需记忆复杂命令。
- **多端点管理**:
    - 自动保存连接过的 `mihomo` 端点（地址和密钥）。
    - 启动时可从已保存列表中快速选择。
    - 支持添加新的端点。
    - 支持 HTTP 和 Unix Domain Socket 连接。
- **实时监控面板**:
    - **概览 (Overview)**: 实时显示上/下行流量、内存使用和内核版本。
    - **连接 (Connections)**: 实时展示当前的活动连接列表、总连接数和累计流量。
- **强大的设置菜单**:
    - **模式切换**: 循环切换 `规则` / `全局` / `直连` 模式，并开关 `TUN` 模式。
    - **重载与重启**: 独立地重载配置文件、GEO 数据库，或重启 `mihomo` 核心。
    - **一键升级**: 在线升级内核、UI 面板和 GEO 数据库。
    - **查看完整配置**: 显示当前 `mihomo` 的全部运行配置。

## 安装与运行

### 1. 环境要求
- Python 3.7+
- `mihomo` 核心已在运行，并开启了外部控制 API。

### 2. 克隆项目
```bash
git clone https://github.com/your-username/mihomo-controller.git
cd mihomo-controller
```
*(请将 `your-username` 替换为您的 GitHub 用户名)*

### 3. 安装依赖
建议在 Python 虚拟环境中进行操作，以避免依赖冲突。

```bash
# 创建虚拟环境 (可选)
python -m venv venv

# 安装所有必需的库
pip install -r requirements.txt
```

### 4. 运行程序
确保 `mihomo` 正在运行，然后执行：
```bash
python scripts/run.py
```
程序启动后，会提示您选择一个已保存的 `mihomo` 端点或添加一个新的端点。

## 未来计划
不知道，取决于灵感 
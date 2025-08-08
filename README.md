# Clash Controller

一个使用 `InquirerPy` 构建的、功能丰富的 `clash` 文本用户界面（TUI）控制器。它可以让您方便地通过命令行管理和监控一个或多个 `clash` 实例。

## 功能特性

- **交互式 TUI 界面**: 友好的菜单驱动操作，无需记忆复杂命令。
- **多端点管理**:
    - 自动保存连接过的 Clash 端点（地址和密钥）。
    - 启动时可从已保存列表中快速选择。
    - 支持添加新的端点。
    - 支持 HTTP 和 Unix Domain Socket 连接。
- **实时监控面板**:
    - **概览 (Overview)**: 实时显示上/下行流量、内存使用和内核版本。
    - **连接 (Connections)**: 实时展示当前的活动连接列表、总连接数和累计流量。
- **强大的设置菜单**:
    - **模式切换**: 循环切换 `规则` / `全局` / `直连` 模式，并开关 `TUN` 模式。
    - **重载与重启**: 独立地重载配置文件、GEO 数据库，或重启 Clash 核心。
    - **一键升级**: 在线升级内核、UI 面板和 GEO 数据库。
    - **查看完整配置**: 显示当前 Clash 的全部运行配置。

## 安装

```bash
pip install clash-controller
```

## 使用方法

安装后，可以通过以下命令启动程序：

```bash
clashctl
```

程序启动后，会提示您选择一个已保存的 Clash 端点或添加一个新的端点。

## 开发者安装

如果你想要从源代码运行或者参与开发：

```bash
git clone https://github.com/Moha-Master/clash-controller.git
cd clash-controller

# 创建虚拟环境 (推荐)
python -m venv venv
source venv/bin/activate  # 在 Windows 上使用 venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 运行程序
python -m clash_controller
```

## 要求

- Python 3.8+
- 运行中的 Clash 实例，已开启外部控制 API 
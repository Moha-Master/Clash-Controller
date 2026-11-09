"""命令行入口：解析参数、降噪日志并启动 Textual TUI。"""
import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import CONFIG_DIR_ENV, DEFAULT_CONFIG_DIR
from .platform_utils import HELPER_FLAG


def _setup_logging() -> None:
    logging.getLogger().setLevel(logging.CRITICAL)
    for name in ("urllib3", "requests", "paramiko", "charset_normalizer"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def parse_args():
    parser = argparse.ArgumentParser(description="Clash 控制器（Textual TUI）")
    parser.add_argument(
        "-D",
        "--dir",
        help="配置目录路径",
        default=str(Path(DEFAULT_CONFIG_DIR).expanduser()),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用 API / SSH 调试日志（输出到应用日志页）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"clashctl {__version__}",
    )
    return parser.parse_args()


def main() -> int:
    # 提权 helper：打包成 exe 后 sys.executable 就是本程序，UAC 提权重启时由这里分派；
    # pip 安装则直接走 `python -m clash_controller.elevated_helper`，不经过本函数。
    argv = sys.argv[1:]
    if argv[:1] == [HELPER_FLAG]:
        from .elevated_helper import main as elevated_main

        return elevated_main(argv[1:])

    _setup_logging()
    args = parse_args()
    os.environ[CONFIG_DIR_ENV] = args.dir

    from .app import ClashControllerApp

    ClashControllerApp(debug=args.debug).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

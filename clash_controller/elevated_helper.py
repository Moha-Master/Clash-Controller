"""Windows 提权写入 helper：由 ``platform_utils`` 通过 UAC 以管理员令牌再启动本包执行。

职责刻意收窄成「读 payload → 原子写目标 → 回写状态」三件事：不读应用配置、不联网、不碰 UI，
所以被提权的进程能做的事非常有限，出错也只可能来自这几个路径。
"""
import argparse
import sys

from .platform_utils import HELPER_FLAG, elevated_apply_payload, write_status

EXIT_OK = 0
EXIT_FAILED = 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=f"clashctl {HELPER_FLAG}",
        description="以管理员权限写入 Clash 配置文件（由 clashctl 内部调用，无需手动执行）",
    )
    parser.add_argument("payload", help="待写入内容所在的临时文件")
    parser.add_argument("target", help="目标 config.yaml 的绝对路径")
    parser.add_argument("backup", help="备份文件路径")
    parser.add_argument("status", help="回写结果的状态文件路径")
    args = parser.parse_args(argv)

    try:
        elevated_apply_payload(args.payload, args.target, args.backup)
    except BaseException as exc:  # noqa: BLE001  # 提权进程必须把任何失败原因带回主进程
        write_status(args.status, f"ERR {type(exc).__name__}: {exc}")
        return EXIT_FAILED
    write_status(args.status, "OK")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

"""SSH / 本地配置文件部署服务层。

由原 cli.py 迁移而来：只负责连接、执行命令与写文件，不产生终端输出；
所有失败统一抛出 RuntimeError，调试信息通过 ``log`` 回调输出。
"""
import io
import os
import posixpath
import subprocess
from collections.abc import Callable

import paramiko

from .platform_utils import (
    IS_WINDOWS,
    default_local_config_dir,
    expand_local_path,
    write_local_config,
)


def _noop(_message: str) -> None:
    pass


def connect_ssh(profile: dict, log: Callable[[str], None] = _noop, debug: bool = False) -> paramiko.SSHClient:
    """根据 profile['ssh'] 建立 SSH 连接，返回已连接的 SSHClient。"""
    ssh_cfg = profile.get('ssh') or {}
    host = ssh_cfg.get('host')
    port = int(ssh_cfg.get('port', 22))
    username = ssh_cfg.get('username', 'root')
    auth_type = ssh_cfg.get('auth_type', 'password')
    ignore_hostkey = bool(ssh_cfg.get('ignore_hostkey', False))

    client = paramiko.SSHClient()
    if ignore_hostkey:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    connect_kwargs = {
        'hostname': host,
        'port': port,
        'username': username,
        'timeout': 15,
    }
    if auth_type == 'password':
        connect_kwargs['password'] = ssh_cfg.get('password', '')
        connect_kwargs['look_for_keys'] = False
        connect_kwargs['allow_agent'] = False
    else:
        private_key = ssh_cfg.get('private_key', '')
        private_key_path = ssh_cfg.get('private_key_path', '')
        passphrase = ssh_cfg.get('passphrase', '')
        pkey = None

        if private_key_path:
            # Prefer Paramiko's built-in connect key handling for key files.
            connect_kwargs['key_filename'] = private_key_path
            if passphrase:
                connect_kwargs['passphrase'] = passphrase
            connect_kwargs['look_for_keys'] = False
            connect_kwargs['allow_agent'] = False
        elif private_key:
            # For key text input, load to a PKey object then pass via pkey.
            key_loaders_text = [
                paramiko.RSAKey.from_private_key,
                paramiko.Ed25519Key.from_private_key,
                paramiko.ECDSAKey.from_private_key,
            ]
            dss_cls = getattr(paramiko, 'DSSKey', None)
            if dss_cls is not None:
                key_loaders_text.append(dss_cls.from_private_key)
            key_file = io.StringIO(private_key)
            last_err = None
            for loader in key_loaders_text:
                key_file.seek(0)
                try:
                    pkey = loader(key_file, password=passphrase or None)
                    last_err = None
                    break
                except Exception as e:  # noqa: BLE001 — 逐个尝试不同密钥类型
                    last_err = e
            if pkey is None and last_err is not None:
                raise last_err
            connect_kwargs['pkey'] = pkey
            connect_kwargs['look_for_keys'] = False
            connect_kwargs['allow_agent'] = False
            if passphrase:
                connect_kwargs['passphrase'] = passphrase
        else:
            raise RuntimeError("private key auth selected but no private key file/text provided")

    if debug:
        log(f"[DEBUG][SSH] connecting {username}@{host}:{port} (auth={auth_type})")
    client.connect(**connect_kwargs)
    return client


def run_ssh_command(ssh_client, command: str):
    stdin, stdout, stderr = ssh_client.exec_command(command)
    # 先读完输出再取退出码：否则大输出会把 channel 窗口写满而死锁。
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    code = stdout.channel.recv_exit_status()
    return code, out, err


def run_ssh_command_bytes(ssh_client, command: str):
    """执行命令并按原始字节返回 stdout（用于读取配置文件，避免解码改写内容）。"""
    stdin, stdout, stderr = ssh_client.exec_command(command)
    out = stdout.read()
    err = stderr.read().decode('utf-8', errors='replace')
    code = stdout.channel.recv_exit_status()
    return code, out, err


def run_ssh_command_with_input(ssh_client, command: str, input_text: str):
    stdin, stdout, stderr = ssh_client.exec_command(command)
    if input_text is not None:
        stdin.write(input_text)
        stdin.flush()
    try:
        stdin.channel.shutdown_write()
    except Exception:
        pass
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    code = stdout.channel.recv_exit_status()
    return code, out, err


def _remote_paths(profile: dict) -> tuple[str, str, bool]:
    config_dir = profile.get('config_directory') or '/etc/clash'
    config_path = posixpath.join(config_dir, 'config.yaml')
    ssh_cfg = profile.get('ssh') or {}
    ssh_user = str(ssh_cfg.get('username', 'root')).strip()
    use_remote_sudo = bool(profile.get('use_sudo', True)) and ssh_user != 'root'
    return config_path, config_path + '.bak', use_remote_sudo


def fetch_remote_file_meta_and_content(
    profile: dict,
    log: Callable[[str], None] = _noop,
    debug: bool = False,
) -> tuple[int, bytes]:
    """读取远端 config.yaml 的 mtime 与内容。"""
    config_path, _, use_remote_sudo = _remote_paths(profile)
    sudo_prefix = 'sudo ' if use_remote_sudo else ''

    ssh = connect_ssh(profile, log=log, debug=debug)
    try:
        stat_cmd = f"{sudo_prefix}stat -c %Y:%s -- {config_path}"
        if debug:
            log(f"[DEBUG][SSH] stat_cmd={stat_cmd}")
        code, out, err = run_ssh_command(ssh, stat_cmd)
        if code != 0:
            raise RuntimeError(f"failed to stat remote config: {err.strip() or out.strip()}")
        stat_text = out.strip()
        mtime_str = stat_text.split(':', 1)[0] if stat_text else '0'
        remote_mtime = int(mtime_str)

        cat_cmd = f"{sudo_prefix}cat -- {config_path}"
        if debug:
            log(f"[DEBUG][SSH] cat_cmd={cat_cmd}")
        code, raw, err = run_ssh_command_bytes(ssh, cat_cmd)
        if code != 0:
            raise RuntimeError(f"failed to read remote config: {err.strip()}")
        return remote_mtime, raw
    finally:
        ssh.close()


def apply_remote_config_via_ssh(
    profile: dict,
    payload_text: str,
    log: Callable[[str], None] = _noop,
    debug: bool = False,
) -> None:
    """备份并通过 SSH 覆写远端 config.yaml。"""
    config_path, bak_path, use_remote_sudo = _remote_paths(profile)
    sudo_prefix = 'sudo ' if use_remote_sudo else ''

    ssh = connect_ssh(profile, log=log, debug=debug)
    try:
        cmd_backup = f"{sudo_prefix}cp -- {config_path} {bak_path}"
        if debug:
            log(f"[DEBUG][SSH] backup_cmd={cmd_backup}")
        code, out, err = run_ssh_command(ssh, cmd_backup)
        if code != 0:
            raise RuntimeError(f"failed to backup remote config: {err.strip() or out.strip()}")

        cmd_replace = f"{sudo_prefix}tee -- {config_path}"
        if debug:
            log(f"[DEBUG][SSH] replace_cmd={cmd_replace}")
        code, out, err = run_ssh_command_with_input(ssh, cmd_replace, payload_text)
        if code != 0:
            raise RuntimeError(f"failed to replace remote config: {err.strip() or out.strip()}")
    finally:
        ssh.close()


def apply_local_config_file(
    profile: dict,
    payload_text: str,
    log: Callable[[str], None] = _noop,
) -> None:
    """备份并覆写本地 config.yaml。

    Windows：先按当前权限原子写入，受 UAC 保护的目录再弹出授权（需要档案开启
    ``use_sudo``，界面显示为“管理员写入 (UAC)”）。
    POSIX：保持原有 sudo 行为（``use_sudo``）。
    """
    config_dir = expand_local_path(profile.get('config_directory') or default_local_config_dir())
    config_path = os.path.join(config_dir, 'config.yaml')
    bak_path = os.path.join(config_dir, 'config.yaml.bak')
    use_elevation = bool(profile.get('use_sudo', True))
    payload = payload_text.encode('utf-8')

    if IS_WINDOWS:
        write_local_config(
            config_dir,
            config_path,
            bak_path,
            payload,
            allow_elevation=use_elevation,
            log=log,
        )
        return

    if use_elevation:
        _apply_local_config_with_sudo(config_path, bak_path, payload)
        return

    try:
        write_local_config(
            config_dir,
            config_path,
            bak_path,
            payload,
            allow_elevation=False,
            log=log,
        )
    except PermissionError as exc:
        raise RuntimeError(f"没有权限写入本地配置 {config_path}: {exc}") from exc


def _apply_local_config_with_sudo(config_path: str, bak_path: str, payload: bytes) -> None:
    backup = subprocess.run(['sudo', 'cp', '--', config_path, bak_path], capture_output=True)
    if backup.returncode != 0:
        raise RuntimeError(f"failed to backup local config with sudo: {_describe_command(backup)}")

    # 用 bytes 作为 stdin，避免 Windows/非 UTF-8 locale 下的文本模式编码问题。
    replace = subprocess.run(['sudo', 'tee', '--', config_path], input=payload, capture_output=True)
    if replace.returncode != 0:
        raise RuntimeError(f"failed to replace local config with sudo: {_describe_command(replace)}")


def _describe_command(completed: subprocess.CompletedProcess) -> str:
    raw = completed.stderr or completed.stdout or b''
    return raw.decode('utf-8', errors='replace').strip()

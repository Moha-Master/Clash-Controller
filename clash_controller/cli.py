import os
import json
import sys
import time
import threading
import queue
from InquirerPy import inquirer
from InquirerPy.validator import EmptyInputValidator
from InquirerPy.base.control import Choice, Separator
import requests # Need to import for requests.exceptions.RequestException
import os.path
import io
import hashlib
import posixpath
import subprocess
from urllib.parse import urlparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import paramiko

from .api import ClashAPI

# ANSI Color Codes
COLOR_GREEN = '\033[92m'
COLOR_YELLOW = '\033[93m'
COLOR_RED = '\033[91m'
COLOR_RESET = '\033[0m'

# Path for storing connection profiles in the user's home directory
PROFILE_PATH = os.path.expanduser("~/.config/clash-controller/profiles.json")
CONFIG_PROVIDERS_PATH = os.path.expanduser("~/.config/clash-controller/config_providers.json")


def _default_endpoint_type(url: str) -> str:
    try:
        host = (urlparse(url).hostname or '').lower()
        if host in ('127.0.0.1', 'localhost', '::1'):
            return 'local'
    except Exception:
        pass
    return 'remote'


def _default_ssh_host(url: str) -> str:
    try:
        host = urlparse(url).hostname
        if host:
            return host
    except Exception:
        pass
    return '127.0.0.1'


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _connect_ssh(profile: dict):
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
                except Exception as e:
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

    client.connect(**connect_kwargs)
    return client


def _ssh_run(ssh_client, command: str):
    stdin, stdout, stderr = ssh_client.exec_command(command)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    return code, out, err


def _fetch_remote_file_meta_and_content(profile: dict):
    config_dir = profile.get('config_directory', '/etc/clash')
    config_path = posixpath.join(config_dir, 'config.yaml')
    use_sudo = bool(profile.get('use_sudo', True))
    ssh_cfg = profile.get('ssh') or {}
    ssh_user = str(ssh_cfg.get('username', 'root')).strip()
    use_remote_sudo = use_sudo and ssh_user != 'root'

    ssh = _connect_ssh(profile)
    try:
        sudo_prefix = 'sudo ' if use_remote_sudo else ''
        # Avoid nested shell quoting here; keep command simple.
        stat_cmd = f"{sudo_prefix}stat -c %Y:%s -- {config_path}"
        if profile.get('debug_ssh'):
            print(f"[DEBUG][SSH] stat_cmd={stat_cmd}")
        code, out, err = _ssh_run(ssh, stat_cmd)
        if code != 0:
            raise RuntimeError(f"failed to stat remote config: {err.strip() or out.strip()}")
        stat_text = out.strip()
        mtime_str = stat_text.split(':', 1)[0] if stat_text else '0'
        remote_mtime = int(mtime_str)

        cat_cmd = f"{sudo_prefix}cat -- {config_path}"
        if profile.get('debug_ssh'):
            print(f"[DEBUG][SSH] cat_cmd={cat_cmd}")
        code, out, err = _ssh_run(ssh, cat_cmd)
        if code != 0:
            raise RuntimeError(f"failed to read remote config: {err.strip() or out.strip()}")
        content = out.encode('utf-8')
        return remote_mtime, content
    finally:
        ssh.close()


def _ssh_run_with_input(ssh_client, command: str, input_text: str):
    stdin, stdout, stderr = ssh_client.exec_command(command)
    if input_text is not None:
        stdin.write(input_text)
        stdin.flush()
    try:
        stdin.channel.shutdown_write()
    except Exception:
        pass
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    return code, out, err


def _apply_remote_config_via_ssh(profile: dict, payload_text: str):
    config_dir = profile.get('config_directory', '/etc/clash')
    config_path = posixpath.join(config_dir, 'config.yaml')
    bak_path = posixpath.join(config_dir, 'config.yaml.bak')
    use_sudo = bool(profile.get('use_sudo', True))
    ssh_cfg = profile.get('ssh') or {}
    ssh_user = str(ssh_cfg.get('username', 'root')).strip()
    use_remote_sudo = use_sudo and ssh_user != 'root'

    ssh = _connect_ssh(profile)
    try:
        sudo_prefix = 'sudo ' if use_remote_sudo else ''
        cmd_backup = f"{sudo_prefix}cp -- {config_path} {bak_path}"
        if profile.get('debug_ssh'):
            print(f"[DEBUG][SSH] backup_cmd={cmd_backup}")
        code, out, err = _ssh_run(ssh, cmd_backup)
        if code != 0:
            raise RuntimeError(f"failed to backup remote config: {err.strip() or out.strip()}")

        cmd_replace = f"{sudo_prefix}tee -- {config_path}"
        if profile.get('debug_ssh'):
            print(f"[DEBUG][SSH] replace_cmd={cmd_replace}")
        code, out, err = _ssh_run_with_input(ssh, cmd_replace, payload_text)
        if code != 0:
            raise RuntimeError(f"failed to replace remote config: {err.strip() or out.strip()}")

    finally:
        ssh.close()


def _apply_local_config_file(profile: dict, payload_text: str):
    config_dir = profile.get('config_directory', '/etc/clash')
    config_path = os.path.join(config_dir, 'config.yaml')
    bak_path = os.path.join(config_dir, 'config.yaml.bak')
    use_sudo = bool(profile.get('use_sudo', True))

    if use_sudo:
        backup_cmd = ['sudo', 'cp', config_path, bak_path]
        backup = subprocess.run(backup_cmd, capture_output=True, text=True)
        if backup.returncode != 0:
            raise RuntimeError(f"failed to backup local config with sudo: {(backup.stderr or backup.stdout).strip()}")

        replace_cmd = ['sudo', 'tee', config_path]
        replace = subprocess.run(replace_cmd, input=payload_text, capture_output=True, text=True)
        if replace.returncode != 0:
            raise RuntimeError(f"failed to replace local config with sudo: {(replace.stderr or replace.stdout).strip()}")
        return

    os.makedirs(config_dir, exist_ok=True)
    if os.path.exists(config_path):
        os.replace(config_path, bak_path)
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(payload_text)


def get_remote_last_modified(url: str) -> datetime or None:
    """Fetches the Last-Modified header from a remote URL."""
    try:
        response = requests.head(url, timeout=5) # Use HEAD request to get headers only
        response.raise_for_status()
        last_modified = response.headers.get('Last-Modified')
        if last_modified:
            return parsedate_to_datetime(last_modified)
    except requests.exceptions.RequestException as e:
        add_log(f"Error fetching Last-Modified for {url}: {e}")
    return None

# Global list to store application logs
app_logs = []

def is_local_api(api: ClashAPI) -> bool:
    """Checks if the API base URL points to a local address."""
    if not api or not api.base_url:
        return False
    
    # Extract hostname from URL
    try:
        from urllib.parse import urlparse
        parsed_url = urlparse(api.base_url)
        hostname = parsed_url.hostname
    except ImportError:
        # Fallback for older Python versions or if urlparse is not available
        # This is a simplified check and might not cover all edge cases
        if "127.0.0.1" in api.base_url or "localhost" in api.base_url or "::1" in api.base_url:
            return True
        return False

    if hostname in ["127.0.0.1", "localhost", "::1"]:
        return True
    return False

def add_log(message: str):
    """Adds a timestamped message to the application log."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    app_logs.append(f"[{timestamp}] {message}")

def show_logs_screen():
    """Displays the accumulated application logs."""
    os.system('cls' if os.name == 'nt' else 'clear')
    print("--- Application Logs ---")
    print("-" * 80)
    if not app_logs:
        print("No logs yet.")
    else:
        for log_entry in app_logs:
            print(log_entry)
    print("-" * 80)
    input("Press Enter to return to the settings menu...")


def handle_api_result(api: ClashAPI, action_desc: str, result, error):
    """Unified handling for API results.

    - If error is None -> treat as success and log it.
    - If error equals ClashAPI.SENT_BUT_DISCONNECTED -> notify user the request was sent but
      the connection was interrupted; advise manual verification.
    - Otherwise -> treat as failure and show error.
    """
    if error is None:
        # Prefer concise, user-friendly messages derived from the API result.
        def _clean_msg(raw: str) -> str:
            if not raw:
                return ''
            s = raw.strip()
            # remove common prefixes
            for prefix in ('update error:', 'error:', 'message:', 'update:'):
                if s.lower().startswith(prefix):
                    s = s[len(prefix):].strip()
                    break
            # Capitalize first letter
            if s:
                s = s[0].upper() + s[1:]
            return s

        short_msg = ''
        status_code = None
        if isinstance(result, dict):
            status_code = result.get('status_code') or result.get('status') if isinstance(result.get('status'), int) else None
            status_val = str(result.get('status', '')).lower()
            # Prefer explicit message/info keys
            if 'message' in result:
                short_msg = _clean_msg(str(result['message']))
            elif 'info' in result:
                short_msg = _clean_msg(str(result['info']))
            elif 'body' in result and isinstance(result['body'], str):
                short_msg = _clean_msg(result['body'])

            # Determine success heuristics: explicit status value or HTTP-like code
            success = False
            if status_val in ('success', 'ok', 'done', 'true'):
                success = True
            if isinstance(status_code, int) and status_code in (200, 201, 202, 204):
                success = True

            if success:
                print(f"Successfully {action_desc}.")
                if short_msg:
                    print(f"  Note: {short_msg}")
                add_log(f"Successfully {action_desc}. {short_msg}")
            else:
                # Not an explicit success; surface the message as info/warning
                if short_msg:
                    print(f"{action_desc}: {short_msg}")
                    add_log(f"{action_desc}: {short_msg}")
                else:
                    print(f"{action_desc} completed. (see details)")
                    add_log(f"{action_desc} completed with no short message.")

            # In debug mode, also print the full result JSON for inspection
            if api and getattr(api, 'debug', False):
                try:
                    import json as _json
                    pretty = _json.dumps(result, indent=2, ensure_ascii=False)
                    print(f"[DEBUG] Full API result:\n{pretty}")
                except Exception:
                    print(f"[DEBUG] Full API result: {result}")

            return True

        # Non-dict results: just print a concise line and debug details if available
        try:
            s = str(result)
            if s:
                print(f"{action_desc} completed: {s}")
                add_log(f"{action_desc} completed: {s}")
            else:
                print(f"Successfully {action_desc}.")
                add_log(f"Successfully {action_desc}.")
        except Exception:
            print(f"Successfully {action_desc}.")
            add_log(f"Successfully {action_desc}.")
        return True

    # Handle sentinel for sent-but-disconnected
    try:
        sentinel = api.SENT_BUT_DISCONNECTED
    except Exception:
        sentinel = None

    if error == sentinel:
        print(f"Request '{action_desc}' was sent but the connection was interrupted before a final response could be read.")
        print("The operation may have been applied. Please check Clash logs/status manually to confirm.")
        add_log(f"Request '{action_desc}' may have been applied but connection closed before response.")
        return None

    # Generic failure
    # If the API included an info body, show it even when error happened
    if isinstance(result, dict) and 'info' in result and result.get('info'):
        info = str(result.get('info'))[:2000]
        print(f"{action_desc} completed: {info}")
        add_log(f"{action_desc} completed with info: {info}")
        return True

    print(f"Failed to {action_desc}: {error}")
    add_log(f"Failed to {action_desc}: {error}")
    return False

def load_profiles():
    """Loads connection profiles from the config file."""
    if not os.path.exists(PROFILE_PATH):
        return []
    try:
        with open(PROFILE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        print(f"Warning: Could not read or parse profiles file at {PROFILE_PATH}")
        return []

def save_profiles(profiles):
    """Saves connection profiles to the config file."""
    try:
        os.makedirs(os.path.dirname(PROFILE_PATH), exist_ok=True)
        with open(PROFILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(profiles, f, indent=4, ensure_ascii=False)
    except IOError as e:
        print(f"Error saving profiles to {PROFILE_PATH}: {e}")

def load_config_providers():
    """Loads config provider URLs from the config file."""
    if not os.path.exists(CONFIG_PROVIDERS_PATH):
        return []
    try:
        with open(CONFIG_PROVIDERS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        print(f"Warning: Could not read or parse config providers file at {CONFIG_PROVIDERS_PATH}")
        return []

def save_config_providers(providers):
    """Saves config provider URLs to the config file."""
    try:
        os.makedirs(os.path.dirname(CONFIG_PROVIDERS_PATH), exist_ok=True)
        with open(CONFIG_PROVIDERS_PATH, 'w', encoding='utf-8') as f:
            json.dump(providers, f, indent=4, ensure_ascii=False)
    except IOError as e:
        print(f"Error saving config providers to {CONFIG_PROVIDERS_PATH}: {e}")

def _stream_fetcher(api_method, data_queue, stop_event):
    """
    A worker function to run in a thread. 
    It fetches data from a streaming API endpoint and puts it into a queue.
    """
    try:
        response, error = api_method() # API now returns (data, error)
        if error:
            add_log(f"Stream fetcher error: {error}")
            data_queue.put(None) # Signal stream end due to error
            return

        if response:
            for line in response.iter_lines():
                if stop_event.is_set():
                    break
                if line:
                    try:
                        json_str = line.decode('utf-8').lstrip('data: ')
                        if json_str:
                            data_queue.put(json.loads(json_str))
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        add_log(f"Malformed stream line: {line.decode('utf-8', errors='ignore')} - Error: {e}")
                        continue # Ignore malformed lines
    except requests.exceptions.RequestException as e:
        add_log(f"Stream connection error: {e}")
        pass
    finally:
        # Signal that this stream has ended, e.g., for error display
        data_queue.put(None) 

def show_connections_page(api: ClashAPI):
    """Displays active connections, refreshing periodically."""
    try:
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("Active Connections (Press Ctrl+C to return)")
            print("-" * 80)
            
            connections_data, error = api.get_connections()
            if error:
                print(f"Error retrieving connections: {error}")
                add_log(f"Error retrieving connections: {error}")
                time.sleep(2) # Give user time to read error
                break # Exit connections page on error
            
            if connections_data and 'connections' in connections_data:
                connections = connections_data['connections']
                total_dl = connections_data.get('downloadTotal', 0) / (1024*1024)
                total_ul = connections_data.get('uploadTotal', 0) / (1024*1024)

                print(f"Total Connections: {len(connections)} | Total UL/DL: {total_ul:.2f}MB / {total_dl:.2f}MB")
                print("-" * 80)
                
                # Header
                print(f"{'Host':<30} {'Network':<7} {'Type':<10} {'Rule':<12} {'Chains'}")
                print(f"{'-'*30:<30} {'-'*7:<7} {'-'*10:<10} {'-'*12:<12} {'-'*15}")

                # Display first 20 connections to avoid clutter
                for conn in connections[:20]:
                    metadata = conn.get('metadata', {})
                    host = metadata.get('host') or metadata.get('destinationIP', 'N/A')
                    network = metadata.get('network', 'N/A')
                    conn_type = metadata.get('type', 'N/A')
                    rule = conn.get('rule', 'N/A')
                    chains = " -> ".join(conn.get('chains', []))
                    
                    # Truncate long hostnames
                    if len(host) > 28:
                        host = host[:25] + "..."

                    print(f"{host:<30} {network:<7} {conn_type:<10} {rule:<12} {chains}")

                if len(connections) > 20:
                    print(f"\n... and {len(connections) - 20} more connections.")

            else:
                print("Could not retrieve connections or no active connections.")

            print("-" * 80)
            print(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            time.sleep(1) # Refresh interval
            
    except KeyboardInterrupt:
        print("\nReturning to main menu...")
        time.sleep(0.5)

def show_overview_page(api: ClashAPI):
    """Displays the overview page with real-time stats using streaming."""
    version_info, error = api.get_version()
    version = version_info.get('version', 'N/A') if version_info else 'N/A'
    if error:
        add_log(f"Error fetching version for overview: {error}")
        version = f"N/A (Error: {error})"
    
    stop_event = threading.Event()
    traffic_queue = queue.Queue()
    memory_queue = queue.Queue()

    traffic_thread = threading.Thread(
        target=_stream_fetcher, args=(api.get_traffic_stream, traffic_queue, stop_event), daemon=True
    )
    memory_thread = threading.Thread(
        target=_stream_fetcher, args=(api.get_memory_stream, memory_queue, stop_event), daemon=True
    )

    traffic_thread.start()
    memory_thread.start()

    latest_traffic = {"up": 0, "down": 0}
    latest_memory = {"inuse": 0}
    streams_alive = True

    try:
        while streams_alive:
            # Check for new traffic data
            try:
                traffic_data = traffic_queue.get_nowait()
                if traffic_data is None:
                    streams_alive = False
                    break
                latest_traffic = traffic_data
            except queue.Empty:
                pass

            # Check for new memory data
            try:
                memory_data = memory_queue.get_nowait()
                if memory_data is None:
                    streams_alive = False
                    break
                latest_memory = memory_data
            except queue.Empty:
                pass

            # --- Render UI ---
            os.system('cls' if os.name == 'nt' else 'clear')
            print("Clash Overview (Press Ctrl+C to go back to Main Menu)")
            print("-" * 50)
            print(f"  Version: {version}")
            print("-" * 50)
            
            # Display Traffic
            up_kbs = latest_traffic.get('up', 0) / 1024
            down_kbs = latest_traffic.get('down', 0) / 1024
            print("  Traffic:")
            print(f"    Upload:   {up_kbs:.2f} KB/s")
            print(f"    Download: {down_kbs:.2f} KB/s")

            # Display Memory
            mem_mb = latest_memory.get('inuse', 0) / (1024 * 1024)
            print("\n  Memory:")
            print(f"    In Use: {mem_mb:.2f} MB")
            
            print("-" * 50)
            print(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            time.sleep(0.5) # Refresh rate for the screen

        if not streams_alive:
            print("\nConnection to a real-time data stream was lost.")
            add_log("Real-time data stream lost.")
            input("Press Enter to return to the main menu...")

    except KeyboardInterrupt:
        pass # User requested to go back
    finally:
        # --- Cleanup ---
        stop_event.set() # Tell threads to stop
        # The threads are daemons, they will exit anyway, but this is cleaner.
        print("\nReturning to main menu...")
        time.sleep(0.5) # Give a moment for the message to be seen

def show_config_menu(api: ClashAPI):
    """Displays the configuration sub-menu and handles user actions."""
    while True:

        try:
            config_providers = load_config_providers()

            provider_choices = [
                Choice(name=f"{p['name']} ({p['url']})", value=p) for p in config_providers
            ]
            provider_choices.extend([
                Separator(),
                Choice(name="Add new config provider", value="new"),
                Choice(name="Reload Config File", value="reload_local"),
                Separator(),
                Choice(name="Back to Main Menu", value="back"),
            ])

            action = inquirer.select(
                message="Configuration Menu",
                choices=provider_choices,
                default=None,
            ).execute()

            if action == "new":
                try:
                    url = inquirer.text(
                        message="Enter config file URL (e.g., http://example.com/config.yaml):",
                        validate=EmptyInputValidator()
                    ).execute()
                    provider_name = inquirer.text(
                        message="Enter a name for this config provider:",
                        default=url,
                        validate=EmptyInputValidator()
                    ).execute()
                except KeyboardInterrupt:
                    add_log("New config provider creation cancelled by user (KeyboardInterrupt).")
                    continue

                new_provider = {"name": provider_name, "url": url}
                config_providers.append(new_provider)
                save_config_providers(config_providers)
                add_log(f"New config provider '{provider_name}' added.")
                print(f"New config provider '{provider_name}' added.")

            elif action == "reload_local":
                print("\nReloading config file...")
                result, error = api.reload_configs()
                handle_api_result(api, "Reload config", result, error)

            elif action == "back":
                return None
            elif action: # A saved config provider was selected
                provider_url = action['url']

                print(f"Fetching config from {provider_url}...")
                add_log(f"Fetching config from {provider_url}...")

                try:
                    response = requests.get(provider_url, timeout=30)
                    response.raise_for_status()
                    remote_payload = response.text
                    remote_bytes = remote_payload.encode('utf-8')
                    remote_hash = _compute_sha256(remote_bytes)
                    remote_mod_time = get_remote_last_modified(provider_url)

                    # Find current selected profile details for SCP path and endpoint mode
                    profiles = load_profiles()
                    current_profile = None
                    for p in profiles:
                        if p.get('url') == api.base_url and p.get('secret') == (api.headers.get('Authorization', '').replace('Bearer ', '') if api.headers.get('Authorization') else None):
                            current_profile = p
                            break
                    if current_profile is None:
                        for p in profiles:
                            if p.get('url') == api.base_url:
                                current_profile = p
                                break
                    if current_profile is None:
                        raise RuntimeError("active profile not found in local profiles.json")

                    endpoint_type = current_profile.get('endpoint_type', _default_endpoint_type(current_profile.get('url', '')))
                    if '--debug' in sys.argv:
                        current_profile['debug_ssh'] = True

                    if endpoint_type == 'remote':
                        local_display_path = posixpath.join(current_profile.get('config_directory', '/etc/clash'), 'config.yaml')
                        print(f"Comparing remote provider file with endpoint config: {local_display_path}")
                        add_log(f"Comparing remote provider file with endpoint config: {local_display_path}")
                        local_mtime, local_bytes = _fetch_remote_file_meta_and_content(current_profile)
                    else:
                        config_dir = current_profile.get('config_directory', '/etc/clash')
                        local_display_path = os.path.join(config_dir, 'config.yaml')
                        print(f"Comparing remote provider file with local config: {local_display_path}")
                        add_log(f"Comparing remote provider file with local config: {local_display_path}")
                        if not os.path.exists(local_display_path):
                            raise RuntimeError(f"local config file not found: {local_display_path}")
                        local_mtime = int(os.path.getmtime(local_display_path))
                        with open(local_display_path, 'rb') as f:
                            local_bytes = f.read()

                    local_hash = _compute_sha256(local_bytes)

                    proceed_update = True
                    if remote_hash != local_hash:
                        if remote_mod_time is not None:
                            remote_ts = int(remote_mod_time.timestamp())
                            if remote_ts > local_mtime:
                                print("Remote config is newer and content differs. Will update automatically.")
                                add_log("Remote config newer with different hash. Proceeding without prompt.")
                            else:
                                print("Remote config content differs, but timestamp is same or older than target config.")
                                confirm = inquirer.confirm(
                                    message="Remote config is not newer. Overwrite target config anyway?",
                                    default=False
                                ).execute()
                                proceed_update = bool(confirm)
                        else:
                            print("Remote config content differs, but remote Last-Modified is unavailable.")
                            confirm = inquirer.confirm(
                                message="Proceed to overwrite target config?",
                                default=True
                            ).execute()
                            proceed_update = bool(confirm)
                    else:
                        print("Remote and target config file content are identical.")
                        confirm = inquirer.confirm(
                            message="Content is identical. Overwrite target config anyway?",
                            default=False
                        ).execute()
                        proceed_update = bool(confirm)

                    if not proceed_update:
                        print("Update cancelled by user.")
                        add_log("Config update cancelled by user after comparison.")
                        input("Press Enter to continue...")
                        continue

                    if endpoint_type == 'remote':
                        print("Deploying config to remote endpoint via SSH stream...")
                        add_log("Deploying config to remote endpoint via SSH stream.")
                        _apply_remote_config_via_ssh(current_profile, remote_payload)
                    else:
                        print("Deploying config to local endpoint file...")
                        add_log("Deploying config to local endpoint file.")
                        _apply_local_config_file(current_profile, remote_payload)

                    print("Reloading config file via Clash API...")
                    add_log("Reloading config file via Clash API after file deployment.")
                    result, error = api.reload_configs()
                    handle_api_result(api, "Reload config", result, error)

                except requests.exceptions.RequestException as e:
                    print(f"Error fetching config: {e}")
                    add_log(f"Error fetching config from {provider_url}: {e}")
                except IOError as e:
                    print(f"Error writing config file: {e}")
                    add_log(f"Error writing config file: {e}")
                except Exception as e:
                    print(f"An unexpected error occurred during remote config apply: {e}")
                    add_log(f"Unexpected error during remote config apply: {e}")
                input("Press Enter to continue...")

        except KeyboardInterrupt:
            add_log("Configuration menu exited by user (KeyboardInterrupt).")
            return None

def show_settings_menu(api: ClashAPI):

    """Displays the settings sub-menu and handles user actions."""
    while True:
        try:
            current_configs, error = api.get_configs()
            if error:
                print(f"Error: Could not fetch settings: {error}. Going back to main menu.")
                add_log(f"Error: Could not fetch settings: {error}.")
                return None

            tun_enabled = current_configs.get('tun', {}).get('enable', False)
            tun_status_str = "ON" if tun_enabled else "OFF"
            current_mode = current_configs.get('mode', 'N/A').capitalize()

            action = inquirer.select(
                message="Settings",
                choices=[
                    Choice(name=f"Toggle TUN Mode (Current: {tun_status_str})", value="toggle_tun"),
                    Choice(name=f"Switch Mode (Current: {current_mode})", value="switch_mode"),
                    Separator(),
                    # Section 2: Reload & Restart
                    Choice(name="Reload GEO Databases", value="reload_geo"),
                    Choice(name="Restart Clash Core", value="restart"),
                    Separator(),
                    # Section 3: Upgrade
                    Choice(name="Upgrade Kernel", value="upgrade_kernel"),
                    Choice(name="Upgrade UI", value="upgrade_ui"),
                    Choice(name="Upgrade GEO Databases", value="upgrade_geo"),
                    Separator(),
                    # Section 4: Endpoint Management
                    Choice(name="Switch Endpoint", value="switch_endpoint"),
                    Choice(name="View Logs", value="view_logs"), # New option
                    Separator(),
                    Choice(name="Back to Main Menu", value="back"),
                ],
            ).execute()

            if action == "toggle_tun":
                new_state = not tun_enabled
                result, error = api.toggle_tun(new_state)
                handle_api_result(api, f"Toggle TUN {'enable' if new_state else 'disable'}", result, error)
            elif action == "switch_mode":
                modes = ['rule', 'global', 'direct']
                current_mode_lower = current_configs.get('mode', 'rule')
                try:
                    current_index = modes.index(current_mode_lower)
                    next_index = (current_index + 1) % len(modes)
                    next_mode = modes[next_index]
                except ValueError:
                    next_mode = 'rule' # Default if current mode is not in list
                result, error = api.set_mode(next_mode)
                handle_api_result(api, f"Switch mode to {next_mode}", result, error)
            elif action == "reload_geo":
                print("\nRequesting GEO databases reload...")
                result, error = api.reload_geo_databases()
                handle_api_result(api, "Reload GEO databases", result, error)
            elif action == "restart":
                print("\nRestarting Clash Core...")
                result, error = api.restart()
                handle_api_result(api, "Restart Clash Core", result, error)
            elif action == "upgrade_kernel":
                print("\nRequesting Kernel upgrade...")
                result, error = api.upgrade_kernel()
                handle_api_result(api, "Upgrade Kernel", result, error)
            elif action == "upgrade_ui":
                print("\nRequesting UI upgrade...")
                result, error = api.upgrade_ui()
                handle_api_result(api, "Upgrade UI", result, error)
            elif action == "upgrade_geo":
                print("\nRequesting GEO databases upgrade...")
                result, error = api.upgrade_geo_databases()
                handle_api_result(api, "Upgrade GEO databases", result, error)
            elif action == "switch_endpoint":
                return "switch_endpoint"
            elif action == "view_logs": # Handle new logs option
                show_logs_screen()
            elif action == "back":
                return None
        except KeyboardInterrupt:
            add_log("Settings menu exited by user (KeyboardInterrupt).")
            return None

def show_main_menu(api: ClashAPI):
    """Displays the main menu and handles user actions."""
    version_info, error = api.get_version()
    version = version_info.get('version', 'unknown') if version_info else 'unknown'
    if error:
        add_log(f"Error fetching version for main menu: {error}")
        version = f"N/A (Error: {error})"

    print(f"\nSuccessfully connected to Clash (version: {version})!")
    add_log(f"Successfully connected to Clash (version: {version}).")

    while True:
        try:
            choices_list = [
                Choice(name="Overview", value="overview"),
                Choice(name="Connections", value="connections"),
            ]

            choices_list.append(Choice(name="Configuration", value="configuration"))

            choices_list.extend([
                Choice(name="Settings", value="settings"),
                Choice(name="Exit", value="exit")
            ])

            action = inquirer.select(
                message="Main Menu",
                choices=choices_list,
                default=None,
            ).execute()

            if action == "overview":
                show_overview_page(api)
            elif action == "connections":
                show_connections_page(api)
            elif action == "configuration":
                show_config_menu(api)
            elif action == "settings":
                result = show_settings_menu(api)
                if result == "switch_endpoint":
                    return "switch_endpoint"
            elif action == "exit":
                print("Exiting...")
                add_log("Application exited by user.")
                return "exit"
        except KeyboardInterrupt:
            print("\nExiting...")
            add_log("Main menu exited by user (KeyboardInterrupt).")
            return "exit"

def main():
    """Main function to run the TUI application."""
    add_log("Application started.")
    # Support a global --debug flag to enable request/response debugging in ClashAPI
    debug_mode = ('--debug' in sys.argv)
    while True:
        profiles = load_profiles()
        
        profile_choices = [
            Choice(name=f"{p['name']} ({p['url']})", value=p) for p in profiles
        ]
        profile_choices.extend([
            Separator(),
            Choice(name="Add a new connection", value="new"),
            Choice(name="Exit", value="exit")
        ])

        try:
            selected_profile = inquirer.select(
                message="Select a Clash connection profile:",
                choices=profile_choices,
                default=None,
            ).execute()
        except KeyboardInterrupt:
            print("\nOperation cancelled by user. Exiting.")
            add_log("Profile selection cancelled by user (KeyboardInterrupt).")
            break

        if selected_profile == "exit" or selected_profile is None:
            add_log("Profile selection exited by user.")
            break
            
        api = None
        if selected_profile == "new":
            try:
                url = inquirer.text(
                    message="Enter Clash controller URL (e.g., http://127.0.0.1:9090):", 
                    validate=EmptyInputValidator()
                ).execute()
                secret = inquirer.text(message="Enter API secret (optional):").execute()
                profile_name = inquirer.text(
                    message="Enter a name for this profile:",
                    default=url,
                    validate=EmptyInputValidator()
                ).execute()
                endpoint_type_default = _default_endpoint_type(url)
                endpoint_type = inquirer.select(
                    message="Select endpoint type:",
                    choices=[
                        Choice(name="local", value="local"),
                        Choice(name="remote", value="remote"),
                    ],
                    default=endpoint_type_default,
                ).execute()

                ssh_config = None
                if endpoint_type == 'remote':
                    ssh_host_default = _default_ssh_host(url)
                    ssh_host = inquirer.text(
                        message="SSH host:",
                        default=ssh_host_default,
                        validate=EmptyInputValidator()
                    ).execute()
                    ssh_port_text = inquirer.text(
                        message="SSH port:",
                        default="22",
                        validate=EmptyInputValidator()
                    ).execute()
                    ssh_user = inquirer.text(
                        message="SSH username:",
                        default="root",
                        validate=EmptyInputValidator()
                    ).execute()
                    auth_type = inquirer.select(
                        message="SSH auth method:",
                        choices=[
                            Choice(name="password", value="password"),
                            Choice(name="private key file", value="key_file"),
                            Choice(name="private key text", value="key_text"),
                        ],
                        default="password",
                    ).execute()
                    ignore_hostkey = inquirer.confirm(
                        message="Ignore host key warning?",
                        default=False,
                    ).execute()

                    ssh_config = {
                        "host": ssh_host,
                        "port": int(ssh_port_text),
                        "username": ssh_user,
                        "ignore_hostkey": bool(ignore_hostkey),
                    }
                    if auth_type == 'password':
                        ssh_config["auth_type"] = "password"
                        ssh_config["password"] = inquirer.secret(message="SSH password:").execute()
                    elif auth_type == 'key_file':
                        ssh_config["auth_type"] = "private_key"
                        ssh_config["private_key_path"] = inquirer.text(
                            message="Private key file path:",
                            validate=EmptyInputValidator()
                        ).execute()
                        ssh_config["passphrase"] = inquirer.secret(message="Key passphrase (optional):").execute()
                    else:
                        ssh_config["auth_type"] = "private_key"
                        ssh_config["private_key"] = inquirer.text(
                            message="Paste private key text:",
                            validate=EmptyInputValidator()
                        ).execute()
                        ssh_config["passphrase"] = inquirer.secret(message="Key passphrase (optional):").execute()

                config_directory = inquirer.text(
                    message="Enter Clash config directory:",
                    default="/etc/clash",
                    validate=EmptyInputValidator()
                ).execute()
                debug_ssh = inquirer.confirm(
                    message="Enable SSH debug logs for this profile?",
                    default=False,
                ).execute()
                use_sudo = inquirer.confirm(
                    message="Use sudo when replacing config file?",
                    default=True,
                ).execute()
            except KeyboardInterrupt:
                print("\nOperation cancelled by user. Exiting.")
                add_log("New profile creation cancelled by user (KeyboardInterrupt).")
                break
            
            new_profile = {
                "name": profile_name,
                "url": url,
                "secret": secret,
                "endpoint_type": endpoint_type,
                "config_directory": config_directory,
                "use_sudo": bool(use_sudo),
                "debug_ssh": bool(debug_ssh),
                "ssh": ssh_config,
            }
            profiles.append(new_profile)
            save_profiles(profiles)
            add_log(f"New profile '{profile_name}' added.")
            
            api = ClashAPI(base_url=url, secret=secret, working_directory=config_directory, debug=debug_mode)
        elif selected_profile:
            # Find the actual profile object in the profiles list
            current_profile_obj = None
            for p in profiles:
                if p['name'] == selected_profile['name'] and p['url'] == selected_profile['url']:
                    current_profile_obj = p
                    break

            if current_profile_obj:
                # Close beta: no backward compatibility fallback for old schema
                required_fields = ['endpoint_type', 'config_directory', 'use_sudo']
                if any(k not in current_profile_obj for k in required_fields):
                    print(f"Profile '{current_profile_obj['name']}' uses an old schema. Please recreate this profile.")
                    add_log(f"Profile '{current_profile_obj['name']}' rejected due to old schema.")
                    continue
                if current_profile_obj.get('endpoint_type') == 'remote' and not current_profile_obj.get('ssh'):
                    print(f"Profile '{current_profile_obj['name']}' is missing SSH settings. Please recreate this profile.")
                    add_log(f"Profile '{current_profile_obj['name']}' missing SSH settings.")
                    continue

                api = ClashAPI(base_url=current_profile_obj['url'], secret=current_profile_obj.get('secret'), working_directory=current_profile_obj['config_directory'], debug=debug_mode)
                add_log(f"Selected profile '{current_profile_obj['name']}'.")
            else:
                # This case should ideally not happen if selected_profile is always from profiles
                print("Error: Selected profile not found in the loaded profiles list.")
                add_log("Error: Selected profile not found in the loaded profiles list.")
                continue

        if api:
            print("Connecting...")
            add_log(f"Attempting to connect to Clash at {api.base_url}...")
            version_info, error = api.get_version()
            if version_info:
                add_log(f"Successfully connected to Clash (version: {version_info.get('version', 'unknown')}).")
                result = show_main_menu(api)
                if result == "switch_endpoint":
                    print("\nReturning to endpoint selection...")
                    add_log("Returning to endpoint selection.")
                    continue
                else:
                    break
            else:
                print(f"\nConnection failed. Please check your URL, secret, and make sure Clash is running. Error: {error}")
                add_log(f"Connection failed to {api.base_url}. Error: {error}")
                try:
                    go_back = inquirer.confirm(message="Go back to endpoint selection?", default=True).execute()
                    if go_back:
                        add_log("User chose to go back to endpoint selection.")
                        continue
                    else:
                        add_log("User chose to exit after connection failure.")
                        break
                except KeyboardInterrupt:
                    print("\nExiting.")
                    add_log("User exited during connection failure prompt (KeyboardInterrupt).")
                    break

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        add_log(f"An unexpected error occurred: {e}")

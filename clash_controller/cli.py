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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .api import ClashAPI

# ANSI Color Codes
COLOR_GREEN = '\033[92m'
COLOR_YELLOW = '\033[93m'
COLOR_RED = '\033[91m'
COLOR_RESET = '\033[0m'

# Path for storing connection profiles in the user's home directory
PROFILE_PATH = os.path.expanduser("~/.config/clash-controller/profiles.json")
CONFIG_PROVIDERS_PATH = os.path.expanduser("~/.config/clash-controller/config_providers.json")
TEMP_CONFIG_DIR = os.path.expanduser("~/.config/clash-controller/temp_configs")
CONFIG_BACKUP_BASE_DIR = os.path.expanduser("~/.config/clash-controller/config_backups")


def _safe_endpoint_key(api: ClashAPI) -> str:
    base = (api.base_url or "unknown").replace("http+unix://", "unix-")
    safe = []
    for ch in base:
        if ch.isalnum() or ch in ('-', '_', '.'):
            safe.append(ch)
        else:
            safe.append('_')
    return ''.join(safe).strip('_') or 'unknown'


def _get_backup_paths(api: ClashAPI):
    endpoint_key = _safe_endpoint_key(api)
    endpoint_dir = os.path.join(CONFIG_BACKUP_BASE_DIR, endpoint_key)
    latest_payload_path = os.path.join(endpoint_dir, "config.latest.yaml")
    backup_payload_path = os.path.join(endpoint_dir, "config.yaml.bak")
    latest_runtime_json = os.path.join(endpoint_dir, "runtime.latest.json")
    backup_runtime_json = os.path.join(endpoint_dir, "runtime.bak.json")
    return {
        "endpoint_dir": endpoint_dir,
        "latest_payload_path": latest_payload_path,
        "backup_payload_path": backup_payload_path,
        "latest_runtime_json": latest_runtime_json,
        "backup_runtime_json": backup_runtime_json,
    }

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

                    backups = _get_backup_paths(api)
                    os.makedirs(backups['endpoint_dir'], exist_ok=True)

                    # Compare with last successfully applied payload cache
                    previous_payload = None
                    if os.path.exists(backups['latest_payload_path']):
                        try:
                            with open(backups['latest_payload_path'], 'r', encoding='utf-8') as f:
                                previous_payload = f.read()
                        except IOError:
                            previous_payload = None

                    if previous_payload is not None and previous_payload == remote_payload:
                        print("Remote config content is identical to last applied payload. Skipping update.")
                        add_log("Remote config content is identical to last applied payload. Skipped update.")
                        input("Press Enter to continue...")
                        continue

                    # Backup previous cached payload/runtime snapshots before applying
                    if os.path.exists(backups['latest_payload_path']):
                        os.replace(backups['latest_payload_path'], backups['backup_payload_path'])
                    runtime_config, runtime_error = api.get_configs()
                    if runtime_error:
                        add_log(f"Warning: failed to fetch runtime config before update: {runtime_error}")
                    elif runtime_config is not None:
                        if os.path.exists(backups['latest_runtime_json']):
                            os.replace(backups['latest_runtime_json'], backups['backup_runtime_json'])
                        with open(backups['latest_runtime_json'], 'w', encoding='utf-8') as f:
                            json.dump(runtime_config, f, indent=2, ensure_ascii=False)

                    print("Applying fetched config via Clash API (PUT /configs?force=true, payload)...")
                    add_log("Applying fetched config via Clash API with payload.")
                    result, error = api.reload_configs(payload=remote_payload)
                    ok = handle_api_result(api, "Apply remote config payload", result, error)

                    # Save payload cache only when API call was accepted/handled
                    if ok:
                        with open(backups['latest_payload_path'], 'w', encoding='utf-8') as f:
                            f.write(remote_payload)
                        print(f"Updated local payload backup at: {backups['latest_payload_path']}")
                        add_log(f"Updated local payload backup at {backups['latest_payload_path']}")

                except requests.exceptions.RequestException as e:
                    print(f"Error fetching config: {e}")
                    add_log(f"Error fetching config from {provider_url}: {e}")
                except IOError as e:
                    print(f"Error writing local backup files: {e}")
                    add_log(f"Error writing local backup files: {e}")
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
                working_directory = inquirer.text(
                    message="Enter Clash working directory (e.g., ~/.config/clash):",
                    default=os.path.expanduser("~/.config/clash"),
                    validate=EmptyInputValidator()
                ).execute()
            except KeyboardInterrupt:
                print("\nOperation cancelled by user. Exiting.")
                add_log("New profile creation cancelled by user (KeyboardInterrupt).")
                break
            
            new_profile = {"name": profile_name, "url": url, "secret": secret, "working_directory": working_directory}
            profiles.append(new_profile)
            save_profiles(profiles)
            add_log(f"New profile '{profile_name}' added.")
            
            api = ClashAPI(base_url=url, secret=secret, working_directory=working_directory, debug=debug_mode)
        elif selected_profile:
            # Find the actual profile object in the profiles list
            current_profile_obj = None
            for p in profiles:
                if p['name'] == selected_profile['name'] and p['url'] == selected_profile['url']:
                    current_profile_obj = p
                    break

            if current_profile_obj:
                # Check for and prompt for working_directory if missing (for backward compatibility)
                if 'working_directory' not in current_profile_obj or not current_profile_obj['working_directory']:
                    print(f"\nProfile '{current_profile_obj['name']}' is missing a working directory.")
                    try:
                        working_directory = inquirer.text(
                            message="Enter Clash working directory for this profile (e.g., ~/.config/clash):",
                            default=os.path.expanduser("~/.config/clash"),
                            validate=EmptyInputValidator()
                        ).execute()
                        current_profile_obj['working_directory'] = working_directory
                        save_profiles(profiles) # Now this should save the updated list
                        add_log(f"Updated profile '{current_profile_obj['name']}' with working directory '{working_directory}'.")
                    except KeyboardInterrupt:
                        print("\nOperation cancelled by user. Returning to profile selection.")
                        add_log("Working directory prompt cancelled by user (KeyboardInterrupt).")
                        continue # Return to profile selection instead of breaking

                api = ClashAPI(base_url=current_profile_obj['url'], secret=current_profile_obj.get('secret'), working_directory=current_profile_obj['working_directory'], debug=debug_mode)
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

import os
import json
import sys
import time
import threading
import queue
from datetime import datetime
from InquirerPy import inquirer
from InquirerPy.validator import EmptyInputValidator
from InquirerPy.base.control import Choice, Separator

from .api import MihomoAPI

# Path for storing connection profiles in the user's home directory
PROFILE_PATH = os.path.expanduser("~/.config/mihomo-controller/profiles.json")

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

def _stream_fetcher(api_method, data_queue, stop_event):
    """
    A worker function to run in a thread. 
    It fetches data from a streaming API endpoint and puts it into a queue.
    """
    try:
        response = api_method()
        if response:
            for line in response.iter_lines():
                if stop_event.is_set():
                    break
                if line:
                    try:
                        json_str = line.decode('utf-8').lstrip('data: ')
                        if json_str:
                            data_queue.put(json.loads(json_str))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue # Ignore malformed lines
    except requests.exceptions.RequestException:
        # The stream might close unexpectedly, which is fine.
        pass
    finally:
        # Signal that this stream has ended, e.g., for error display
        data_queue.put(None) 

def show_overview_page(api: MihomoAPI):
    """Displays the overview page with real-time stats using streaming."""
    version_info = api.get_version()
    version = version_info.get('version', 'N/A') if version_info else 'N/A'
    
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
            print("Mihomo Overview (Press Ctrl+C to go back to Main Menu)")
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
            input("Press Enter to return to the main menu...")

    except KeyboardInterrupt:
        pass # User requested to go back
    finally:
        # --- Cleanup ---
        stop_event.set() # Tell threads to stop
        # The threads are daemons, they will exit anyway, but this is cleaner.
        print("\nReturning to main menu...")
        time.sleep(0.5) # Give a moment for the message to be seen

def show_settings_menu(api: MihomoAPI):
    """Displays the settings sub-menu and handles user actions."""
    while True:
        try:
            current_configs = api.get_configs()
            if not current_configs:
                print("Error: Could not fetch settings. Going back to main menu.")
                return None

            tun_enabled = current_configs.get('tun', {}).get('enable', False)
            tun_status_str = "ON" if tun_enabled else "OFF"
            current_mode = current_configs.get('mode', 'N/A').capitalize()

            action = inquirer.select(
                message="Settings",
                choices=[
                    # Section 1: Mode Switching
                    Choice(name="Show Full Configs", value="show_configs"),
                    Choice(name=f"Toggle TUN Mode (Current: {tun_status_str})", value="toggle_tun"),
                    Choice(name=f"Switch Mode (Current: {current_mode})", value="switch_mode"),
                    Separator(),
                    # Section 2: Reload & Restart
                    Choice(name="Reload Config File", value="reload"),
                    Choice(name="Reload GEO Databases", value="reload_geo"),
                    Choice(name="Restart Mihomo Core", value="restart"),
                    Separator(),
                    # Section 3: Upgrade
                    Choice(name="Upgrade Kernel", value="upgrade_kernel"),
                    Choice(name="Upgrade UI", value="upgrade_ui"),
                    Choice(name="Upgrade GEO Databases", value="upgrade_geo"),
                    Separator(),
                    # Section 4: Endpoint Management
                    Choice(name="Switch Endpoint", value="switch_endpoint"),
                    Separator(),
                    Choice(name="Back to Main Menu", value="back"),
                ],
            ).execute()

            if action == "show_configs":
                print("\nCurrent Mihomo Configs:")
                print(json.dumps(current_configs, indent=2, ensure_ascii=False))
                input("\nPress Enter to continue...")
            elif action == "toggle_tun":
                new_state = not tun_enabled
                if api.toggle_tun(new_state) is not None:
                    print(f"Successfully {'enabled' if new_state else 'disabled'} TUN mode.")
                else:
                    print("Failed to toggle TUN mode.")
            elif action == "switch_mode":
                modes = ['rule', 'global', 'direct']
                current_mode_lower = current_configs.get('mode', 'rule')
                try:
                    current_index = modes.index(current_mode_lower)
                    next_index = (current_index + 1) % len(modes)
                    next_mode = modes[next_index]
                except ValueError:
                    next_mode = 'rule'
                if api.set_mode(next_mode) is not None:
                    print(f"Successfully switched mode to {next_mode.capitalize()}.")
                else:
                    print(f"Failed to switch mode to {next_mode.capitalize()}.")
            elif action == "reload":
                print("\nReloading config file...")
                if api.reload_configs():
                    print("Successfully reloaded config file.")
                else:
                    print("Failed to reload config file.")
            elif action == "reload_geo":
                print("\nRequesting GEO databases reload...")
                if api.reload_geo_databases():
                    print("Successfully requested GEO databases reload.")
                else:
                    print("Failed to request GEO databases reload.")
            elif action == "restart":
                print("\nRestarting Mihomo Core...")
                if api.restart():
                    print("Successfully restarted Mihomo Core.")
                else:
                    print("Failed to restart Mihomo Core.")
            elif action == "upgrade_kernel":
                print("\nRequesting Kernel upgrade...")
                if api.upgrade_kernel():
                    print("Successfully requested Kernel upgrade. Check logs for details.")
                else:
                    print("Failed to request Kernel upgrade.")
            elif action == "upgrade_ui":
                print("\nRequesting UI upgrade...")
                if api.upgrade_ui():
                    print("Successfully requested UI upgrade. Check logs for details.")
                else:
                    print("Failed to request UI upgrade.")
            elif action == "upgrade_geo":
                print("\nRequesting GEO databases upgrade...")
                if api.upgrade_geo():
                    print("Successfully requested GEO databases upgrade. Check logs for details.")
                else:
                    print("Failed to request GEO databases upgrade.")
            elif action == "switch_endpoint":
                return "switch_endpoint"
            elif action == "back":
                return None
        except KeyboardInterrupt:
            return None

def show_main_menu(api: MihomoAPI):
    """Displays the main menu and handles user actions."""
    version_info = api.get_version()
    version = version_info.get('version', 'unknown') if version_info else 'unknown'
    print(f"\nSuccessfully connected to Mihomo (version: {version})!")
    
    while True:
        try:
            action = inquirer.select(
                message="Main Menu",
                choices=[
                    Choice(name="Overview", value="overview"),
                    Choice(name="Settings", value="settings"),
                    Choice(name="Exit", value="exit")
                ],
                default=None,
            ).execute()

            if action == "overview":
                show_overview_page(api)
            elif action == "settings":
                result = show_settings_menu(api)
                if result == "switch_endpoint":
                    return "switch_endpoint"
            elif action == "exit":
                print("Exiting...")
                return "exit"
        except KeyboardInterrupt:
            print("\nExiting...")
            return "exit"

def main():
    """Main function to run the TUI application."""
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
                message="Select a Mihomo connection profile:",
                choices=profile_choices,
                default=None,
            ).execute()
        except KeyboardInterrupt:
            print("\nOperation cancelled by user. Exiting.")
            break

        if selected_profile == "exit" or selected_profile is None:
            break
            
        api = None
        if selected_profile == "new":
            try:
                url = inquirer.text(
                    message="Enter Mihomo controller URL (e.g., http://127.0.0.1:9090):", 
                    validate=EmptyInputValidator()
                ).execute()
                secret = inquirer.text(message="Enter API secret (optional):").execute()
                profile_name = inquirer.text(
                    message="Enter a name for this profile:",
                    default=url,
                    validate=EmptyInputValidator()
                ).execute()
            except KeyboardInterrupt:
                print("\nOperation cancelled by user. Exiting.")
                break
            
            new_profile = {"name": profile_name, "url": url, "secret": secret}
            profiles.append(new_profile)
            save_profiles(profiles)
            
            api = MihomoAPI(base_url=url, secret=secret)
        elif selected_profile:
            api = MihomoAPI(base_url=selected_profile['url'], secret=selected_profile.get('secret'))

        if api:
            print("Connecting...")
            if api.get_version() is not None:
                result = show_main_menu(api)
                if result == "switch_endpoint":
                    print("\nReturning to endpoint selection...")
                    continue
                else:
                    break
            else:
                print("\nConnection failed. Please check your URL, secret, and make sure Mihomo is running.")
                try:
                    go_back = inquirer.confirm(message="Go back to endpoint selection?", default=True).execute()
                    if go_back:
                        continue
                    else:
                        break
                except KeyboardInterrupt:
                    print("\nExiting.")
                    break

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr) 
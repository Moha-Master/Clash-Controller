import requests
from urllib.parse import quote

class ClashAPI:
    def __init__(self, base_url, secret=None, timeout=5, working_directory=None):
        """
        Initializes the Clash API client.

        :param base_url: The base URL of the Clash controller API.
                         (e.g., http://127.0.0.1:9090 or unix:///path/to/socket)
        :param secret: The secret for API authentication.
        :param timeout: Request timeout in seconds.
        :param working_directory: The working directory of the Clash core, used for config paths.
        """
        self.base_url = base_url
        self.timeout = timeout
        self.headers = {}
        self.working_directory = working_directory
        if secret:
            self.headers['Authorization'] = f'Bearer {secret}'

        if self.base_url.startswith('unix://'):
            import requests_unixsocket
            self.session = requests_unixsocket.Session()
            encoded_path = quote(self.base_url.lstrip('unix://'), safe='')
            self.base_url = f'http+unix://{encoded_path}'
        else:
            self.session = requests.Session()

    def _request(self, method, endpoint, params=None, json_data=None, stream=False):
        """Helper method to make requests to the API."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(
                method,
                url,
                headers=self.headers,
                params=params,
                json=json_data,
                timeout=self.timeout,
                stream=stream
            )
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

            # For successful responses, return JSON if content exists, otherwise return a success indicator
            if response.status_code == 204 or not response.content:
                return {"status": "success"}, None
            return response.json(), None
        except requests.exceptions.RequestException as e:
            return None, str(e)

    # === Real-time Data ===
    def get_logs_stream(self):
        """Get real-time logs. Returns a streaming response object for continuous reading."""
        return self._request('GET', '/logs', stream=True)

    def get_traffic_stream(self):
        """Get real-time traffic. Returns a streaming response object for continuous reading."""
        return self._request('GET', '/traffic', stream=True)

    def get_memory_stream(self):
        """Get real-time memory usage. Returns a streaming response object for continuous reading."""
        return self._request('GET', '/memory', stream=True)

    # === General Info & Control ===
    def get_version(self):
        """Get Clash version."""
        return self._request('GET', '/version')

    def flush_fake_ip_cache(self):
        """Flush the fake-ip cache."""
        return self._request('POST', '/cache/fakeip/flush')

    def restart(self, path="", payload=""):
        """Restart Clash core."""
        return self._request('POST', '/restart', json_data={"path": path, "payload": payload} or {})

    # === Configs ===
    def get_configs(self):
        """Get current configurations."""
        return self._request('GET', '/configs')

    def update_configs(self, partial_configs: dict):
        """Update configurations with a partial config."""
        return self._request('PATCH', '/configs', json_data=partial_configs)

    def reload_configs(self, path="", payload=""):
        """Reload configuration from path."""
        return self._request('PUT', '/configs', params={'force': 'true'}, json_data={"path": path, "payload": payload} or {})
    
    def set_mode(self, mode: str):
        """Sets the connection mode ('rule', 'global', 'direct')."""
        return self.update_configs({"mode": mode.lower()})

    def toggle_tun(self, enable: bool):
        """Enable or disable TUN mode."""
        return self.update_configs({"tun": {"enable": enable}})

    # === Upgrade ===
    def upgrade_kernel(self):
        """Request to upgrade the Clash kernel."""
        return self._request('POST', '/upgrade', json_data={})

    def upgrade_ui(self):
        """Request to upgrade the external-ui."""
        return self._request('POST', '/upgrade/ui', json_data={})

    def upgrade_geo_databases(self):
        """Request to upgrade GEO databases from remote."""
        return self._request('POST', '/upgrade/geo', json_data={})

    def reload_geo_databases(self):
        """Request to reload local GEO databases."""
        return self._request('POST', '/configs/geo', json_data={})

    # === Proxies & Groups ===
    def get_proxies(self):
        """Get all proxies and groups information."""
        return self._request('GET', '/proxies')

    def get_proxy(self, name: str):
        """Get a specific proxy or group's information."""
        return self._request('GET', f'/proxies/{quote(name)}')
        
    def select_proxy_in_group(self, group_name: str, proxy_name: str):
        """Select a proxy for a specific group."""
        return self._request('PUT', f'/proxies/{quote(group_name)}', json_data={"name": proxy_name})

    def test_proxy_delay(self, name: str, url: str, timeout: int):
        """Test a proxy's delay."""
        params = {'url': url, 'timeout': str(timeout)}
        return self._request('GET', f'/proxies/{quote(name)}/delay', params=params)

    def get_groups(self):
        """Get policy groups."""
        return self._request('GET', '/group')

    def get_group(self, name: str):
        """Get a specific policy group."""
        return self._request('GET', f'/group/{quote(name)}')

    def reset_auto_group_selection(self, name: str):
        """Reset the fixed selection of an auto policy group."""
        return self._request('DELETE', f'/group/{quote(name)}')

    def test_group_delay(self, name: str, url: str, timeout: int):
        """Test the delay of all proxies in a group."""
        params = {'url': url, 'timeout': str(timeout)}
        return self._request('GET', f'/group/{quote(name)}/delay', params=params)
    
    # === Providers ===
    def get_proxy_providers(self):
        """Get all proxy providers."""
        return self._request('GET', '/providers/proxies')

    def get_proxy_provider(self, name: str):
        """Get a specific proxy provider."""
        return self._request('GET', f'/providers/proxies/{quote(name)}')

    def update_proxy_provider(self, name: str):
        """Update a proxy provider."""
        return self._request('PUT', f'/providers/proxies/{quote(name)}')

    def healthcheck_proxy_provider(self, name: str):
        """Trigger a health check for a proxy provider."""
        return self._request('GET', f'/providers/proxies/{quote(name)}/healthcheck')
    
    def get_rule_providers(self):
        """Get all rule providers."""
        return self._request('GET', '/providers/rules')

    def update_rule_provider(self, name: str):
        """Update a rule provider."""
        return self._request('PUT', f'/providers/rules/{quote(name)}')

    # === Rules ===
    def get_rules(self):
        """Get all rules."""
        return self._request('GET', '/rules')

    # === Connections ===
    def get_connections(self):
        """Get active connections."""
        return self._request('GET', '/connections')

    def close_all_connections(self):
        """Close all active connections."""
        return self._request('DELETE', '/connections')

    def close_connection(self, conn_id: str):
        """Close a specific connection by its ID."""
        return self._request('DELETE', f'/connections/{quote(conn_id)}')

    # === DNS ===
    def query_dns(self, name: str, query_type: str = 'A'):
        """Query DNS."""
        return self._request('GET', '/dns/query', params={'name': name, 'type': query_type}) 
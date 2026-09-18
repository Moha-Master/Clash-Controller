from urllib.parse import quote

import requests


def _noop(_message: str) -> None:
    pass


class ClashAPI:
    # Special sentinel returned when the request was sent but the connection
    # was interrupted (server closed connection / connection reset) and the
    # client cannot determine final outcome. Caller should prompt user to verify.
    SENT_BUT_DISCONNECTED = "SENT_BUT_DISCONNECTED"

    def __init__(self, base_url, secret=None, timeout=10, working_directory=None, debug=False, log=None):
        """
        Initializes the Clash API client.

        :param base_url: The base URL of the Clash controller API.
                         (e.g., http://127.0.0.1:9090 or unix:///path/to/socket)
        :param secret: The secret for API authentication.
        :param timeout: Request timeout in seconds.
        :param working_directory: The working directory of the Clash core, used for config paths.
        :param debug: When True, emit raw request/response info via ``log``.
        :param log: Callable receiving debug strings (defaults to no-op; never prints).
        """
        self.base_url = base_url
        self.timeout = timeout
        self.headers = {}
        self.working_directory = working_directory
        self.debug = bool(debug)
        # Debug output is routed to this callback (never stdout) so it cannot
        # corrupt the TUI.
        self._log = log or _noop
        if secret:
            self.headers['Authorization'] = f'Bearer {secret}'

        if self.base_url.startswith('unix://'):
            import requests_unixsocket
            self.session = requests_unixsocket.Session()
            encoded_path = quote(self.base_url.removeprefix('unix://'), safe='')
            self.base_url = f'http+unix://{encoded_path}'
        else:
            self.session = requests.Session()

    def _is_local(self):
        # Determine if the configured base_url points to a local service.
        try:
            if self.base_url.startswith('http+unix://'):
                return True
            lower = self.base_url.lower()
            return '127.0.0.1' in lower or 'localhost' in lower or '::1' in lower
        except Exception:
            return False

    def _request(self, method, endpoint, params=None, json_data=None, stream=False):
        """Helper method to make requests to the API.

        Behavior changes:
        - Retries once on ReadTimeout with a longer timeout.
        - Treats PUT /configs ReadTimeout conservatively as success (server-side reload may have completed).
        - For streaming requests (stream=True) returns the raw response object so callers can iterate.
        - When debug=True, emits basic request/response information via the log callback.
        """
        url = f"{self.base_url}{endpoint}"

        def do_request(timeout_val):
            return self.session.request(
                method,
                url,
                headers=self.headers,
                params=params,
                json=json_data,
                timeout=timeout_val,
                stream=stream
            )

        try:
            if self.debug:
                try:
                    self._log(f"[DEBUG] Request: {method} {url}\n  headers={self.headers}\n  params={params}\n  json={json_data}\n  stream={stream}\n  timeout={self.timeout}")
                except Exception:
                    pass

            response = do_request(self.timeout)

            # If caller requested streaming, return the raw response so the caller can iterate
            if stream:
                if self.debug:
                    self._log(f"[DEBUG] Response: status={response.status_code} headers={dict(response.headers)} (streaming)")
                return response, None

            # For non-stream responses: always return the API-provided body (prefer JSON),
            # along with no error. We do not auto-raise on 4xx/5xx because the Clash API
            # encodes operation result details in the response body.
            if response.status_code == 204 or not response.content:
                if self.debug:
                    self._log(f"[DEBUG] Response: status={response.status_code} (no content)")
                return {"status": "success", "status_code": response.status_code}, None

            # Try JSON first, fall back to text
            try:
                parsed = response.json()
            except Exception:
                parsed = response.text

            if self.debug:
                raw = (response.text or '')[:4096]
                self._log(f"[DEBUG] Response: status={response.status_code} headers={dict(response.headers)} body(<=4k)={raw}")

            # If the parsed result is a dict, inject status_code for caller convenience
            if isinstance(parsed, dict):
                parsed.setdefault('status_code', response.status_code)
                return parsed, None
            return {"body": parsed, "status_code": response.status_code}, None

        except requests.exceptions.ReadTimeout as e:
            # Retry once with an increased timeout. If retry fails due to
            # connection problems and the API is local, return the sentinel so
            # caller can ask user to verify the outcome manually.
            try:
                longer_timeout = 15
                if self.debug:
                    self._log(f"[DEBUG] ReadTimeout occurred, retrying with timeout={longer_timeout}")
                response = do_request(longer_timeout)
                response.raise_for_status()

                if stream:
                    if self.debug:
                        self._log(f"[DEBUG] Response after retry: status={response.status_code} headers={dict(response.headers)} (streaming)")
                    return response, None

                if response.status_code == 204 or not response.content:
                    if self.debug:
                        self._log(f"[DEBUG] Response after retry: status={response.status_code} (no content)")
                    return {"status": "success"}, None

                if self.debug:
                    raw = response.text[:4096]
                    self._log(f"[DEBUG] Response after retry: status={response.status_code} headers={dict(response.headers)} body(<=4k)={raw}")
                return response.json(), None

            except requests.exceptions.RequestException as exc:
                if self.debug:
                    self._log(f"[DEBUG] ReadTimeout retry failed: {exc}")
                # Reload config may legitimately finish server-side while client times out.
                if method.upper() == 'PUT' and endpoint.startswith('/configs'):
                    return None, self.SENT_BUT_DISCONNECTED
                # If this is a local API, we conservatively cannot determine the final
                # state (server may have applied the operation then closed the connection),
                # so inform the caller to verify manually.
                if self._is_local():
                    return None, self.SENT_BUT_DISCONNECTED
                return None, str(e)

        except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
            # Connection errors/remote close — if local, return sentinel so caller
            # can prompt user to manually verify; otherwise return the error.
            if self.debug:
                self._log(f"[DEBUG] Connection error: {e}")
            if method.upper() == 'PUT' and endpoint.startswith('/configs'):
                return None, self.SENT_BUT_DISCONNECTED
            if self._is_local():
                return None, self.SENT_BUT_DISCONNECTED
            return None, str(e)

        except requests.exceptions.RequestException as e:
            # If requests has attached a response, treat the response body as the
            # authoritative API result (even for 4xx/5xx); only when no response is
            # available do we treat this as a transport-level failure.
            resp = getattr(e, 'response', None)
            if resp is not None:
                try:
                    body = resp.json()
                except Exception:
                    try:
                        body = resp.text
                    except Exception:
                        body = ''

                if self.debug:
                    try:
                        self._log(f"[DEBUG] Error Response: status={resp.status_code} headers={dict(resp.headers)} body(<=4k)={(resp.text or '')[:4096]}")
                    except Exception:
                        self._log(f"[DEBUG] Error Response: status={getattr(resp, 'status_code', 'unknown')}")

                # Return the response body as the result, do not treat as exception
                if isinstance(body, dict):
                    body.setdefault('status_code', resp.status_code)
                    return body, None
                return {"body": body, "status_code": resp.status_code}, None

            # No response available: transport-level error
            if self.debug:
                self._log(f"[DEBUG] RequestException without response (transport error): {e}")
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


def interpret_api_result(action_desc: str, result, error) -> tuple[str, str]:
    """把 ClashAPI 的 ``(result, error)`` 解释为 ``(message, severity)``。

    severity ∈ {"ok", "warn", "err"}；纯逻辑、无副作用，供界面层展示。
    保留 ``SENT_BUT_DISCONNECTED`` 的「已发送但断连」语义。
    """

    def _clean(raw: str) -> str:
        if not raw:
            return ''
        s = raw.strip()
        for prefix in ('update error:', 'error:', 'message:', 'update:'):
            if s.lower().startswith(prefix):
                s = s[len(prefix):].strip()
                break
        if s:
            s = s[0].upper() + s[1:]
        return s

    if error is None:
        if isinstance(result, dict):
            status_val = str(result.get('status', '')).lower()
            status_code = result.get('status_code')
            if 'message' in result:
                short_msg = _clean(str(result['message']))
            elif 'info' in result:
                short_msg = _clean(str(result['info']))
            elif isinstance(result.get('body'), str):
                short_msg = _clean(result['body'])
            else:
                short_msg = ''

            success = status_val in ('success', 'ok', 'done', 'true') or (
                isinstance(status_code, int) and status_code in (200, 201, 202, 204)
            )
            if success:
                return (f"已成功{action_desc}。" + (f" 提示：{short_msg}" if short_msg else ""), "ok")
            if short_msg:
                return (f"{action_desc}: {short_msg}", "warn")
            return (f"{action_desc}已完成（请查看详情）。", "ok")

        text = str(result) if result is not None else ''
        return (f"{action_desc}: {text}" if text else f"已成功{action_desc}。", "ok")

    if error == ClashAPI.SENT_BUT_DISCONNECTED:
        return (
            f"请求“{action_desc}”已发送，但在读取最终响应前连接中断；"
            "该操作可能已生效，请手动检查 Clash 日志 / 状态确认。",
            "warn",
        )

    if isinstance(result, dict) and result.get('info'):
        return (f"{action_desc}完成：{str(result.get('info'))[:2000]}", "ok")

    return (f"{action_desc}失败：{error}", "err")


import json
from urllib.parse import quote

import requests

BODY_ENCODINGS = ("utf-8-sig", "gb18030")


def _noop(_message: str) -> None:
    pass


def _decode_body(response) -> str:
    """按字节显式解码响应体。

    不要直接用 ``response.text``：中间层（反向代理 / CDN）可能把 Content-Type 改成
    没有 charset 的 ``text/*``，requests 会强制按 ISO-8859-1 解码，中文节点名直接乱码。
    """
    data = response.content or b""
    for encoding in BODY_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_body(response):
    """解析响应体：优先按 JSON，失败则退回解码后的文本。"""
    text = _decode_body(response)
    try:
        return json.loads(text)
    except ValueError:
        return text


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

    def _request(
        self, method, endpoint, params=None, json_data=None, stream=False,
        timeout: float | None = None, retry: bool = True,
    ):
        """Helper method to make requests to the API.

        Behavior changes:
        - Retries once on ReadTimeout with a longer timeout.
        - Treats PUT /configs ReadTimeout conservatively as success (server-side reload may have completed).
        - For streaming requests (stream=True) returns the raw response object so callers can iterate.
        - When debug=True, emits basic request/response information via the log callback.
        - ``timeout``（秒）覆盖本次请求超时；``retry=False`` 时 ReadTimeout 不重试（测速不能重复打内核）。
        """
        url = f"{self.base_url}{endpoint}"
        effective_timeout = self.timeout if timeout is None else timeout

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
                    self._log(f"[DEBUG] Request: {method} {url}\n  headers={self.headers}\n  params={params}\n  json={json_data}\n  stream={stream}\n  timeout={effective_timeout}")
                except Exception:
                    pass

            response = do_request(effective_timeout)

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
            parsed = _parse_body(response)

            if self.debug:
                raw = _decode_body(response)[:4096]
                self._log(f"[DEBUG] Response: status={response.status_code} headers={dict(response.headers)} body(<=4k)={raw}")

            # If the parsed result is a dict, inject status_code for caller convenience
            if isinstance(parsed, dict):
                parsed.setdefault('status_code', response.status_code)
                return parsed, None
            return {"body": parsed, "status_code": response.status_code}, None

        except requests.exceptions.ReadTimeout as e:
            # 测速等场景不能重试（会重复打内核），由调用方显式关闭。
            if not retry:
                if self.debug:
                    self._log("[DEBUG] ReadTimeout occurred, retry disabled")
                return None, "请求超时"
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
                    raw = _decode_body(response)[:4096]
                    self._log(f"[DEBUG] Response after retry: status={response.status_code} headers={dict(response.headers)} body(<=4k)={raw}")
                parsed = _parse_body(response)
                if isinstance(parsed, dict):
                    parsed.setdefault('status_code', response.status_code)
                    return parsed, None
                return {"body": parsed, "status_code": response.status_code}, None

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
                    body = _parse_body(resp)
                except Exception:
                    body = ''

                if self.debug:
                    try:
                        self._log(f"[DEBUG] Error Response: status={resp.status_code} headers={dict(resp.headers)} body(<=4k)={_decode_body(resp)[:4096]}")
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
        return self._request('GET', f'/proxies/{quote(name, safe="")}')

    def select_proxy_in_group(self, group_name: str, proxy_name: str):
        """Select a proxy for a specific group."""
        return self._request('PUT', f'/proxies/{quote(group_name, safe="")}', json_data={"name": proxy_name})

    def test_proxy_delay(self, name: str, url: str, timeout: int = 5000, expected=None):
        """Test a proxy's delay (timeout 单位为毫秒，内核限制 <= 32767)。"""
        params = {'url': url, 'timeout': str(timeout)}
        if expected:
            params['expected'] = str(expected)
        return self._request(
            'GET',
            f'/proxies/{quote(name, safe="")}/delay',
            params=params,
            timeout=timeout / 1000 + 10,
            retry=False,
        )

    def get_groups(self):
        """Get policy groups."""
        return self._request('GET', '/group')

    def get_group(self, name: str):
        """Get a specific policy group."""
        return self._request('GET', f'/group/{quote(name, safe="")}')

    def reset_auto_group_selection(self, name: str):
        """Reset the fixed selection of an auto policy group.

        内核没有 ``DELETE /group/{name}``；清除 URLTest / Fallback 的 fixed 走
        ``DELETE /proxies/{name}``（仅 SelectAble 且非 Selector 的组返回 204）。
        """
        return self._request('DELETE', f'/proxies/{quote(name, safe="")}')

    def test_group_delay(self, name: str, url: str, timeout: int = 5000, expected=None):
        """Test the delay of all proxies in a group.

        注意：该端点会先清除 URLTest / Fallback 的 fixed，再并发测试成员。
        """
        params = {'url': url, 'timeout': str(timeout)}
        if expected:
            params['expected'] = str(expected)
        return self._request(
            'GET',
            f'/group/{quote(name, safe="")}/delay',
            params=params,
            timeout=timeout / 1000 + 10,
            retry=False,
        )

    # === Providers ===
    def get_proxy_providers(self):
        """Get all proxy providers."""
        return self._request('GET', '/providers/proxies')

    def get_proxy_provider(self, name: str):
        """Get a specific proxy provider."""
        return self._request('GET', f'/providers/proxies/{quote(name, safe="")}')

    def update_proxy_provider(self, name: str):
        """Update a proxy provider."""
        return self._request('PUT', f'/providers/proxies/{quote(name, safe="")}')

    def healthcheck_proxy_provider(self, name: str):
        """Trigger a health check for a proxy provider."""
        return self._request('GET', f'/providers/proxies/{quote(name, safe="")}/healthcheck')

    def test_provider_proxy_delay(self, provider: str, node: str, url: str, timeout: int = 5000, expected=None):
        """Test a single provider node's delay.

        provider 节点不在 ``/proxies`` 顶层字典里，单测必须走 provider 作用域
        （``/providers/proxies/{provider}/{node}/healthcheck``），否则 404。
        """
        params = {'url': url, 'timeout': str(timeout)}
        if expected:
            params['expected'] = str(expected)
        return self._request(
            'GET',
            f'/providers/proxies/{quote(provider, safe="")}/{quote(node, safe="")}/healthcheck',
            params=params,
            timeout=timeout / 1000 + 10,
            retry=False,
        )

    def get_rule_providers(self):
        """Get all rule providers."""
        return self._request('GET', '/providers/rules')

    def update_rule_provider(self, name: str):
        """Update a rule provider."""
        return self._request('PUT', f'/providers/rules/{quote(name, safe="")}')

    def update_all_providers(self):
        """更新所有代理合集 (proxy-providers) 与规则合集 (rule-providers)。

        由于部分内核（如 Clash 原版）可能不支持 rule-provider 或不完全支持 PUT 批量，
        此处通过遍历获取到的 provider 列表进行逐一更新。
        """
        # 1. 获取所有 Proxy Provider
        proxy_res, err = self.get_proxy_providers()
        if err:
            return None, err
        if isinstance(proxy_res, dict) and proxy_res.get("status_code") in (401, 403):
            return None, f"鉴权失败 (HTTP {proxy_res.get('status_code')})"

        # 2. 获取所有 Rule Provider
        rule_res, err = self.get_rule_providers()
        if err:
            return None, err
        if isinstance(rule_res, dict) and rule_res.get("status_code") in (401, 403):
            return None, f"鉴权失败 (HTTP {rule_res.get('status_code')})"

        # 解析名称列表
        proxy_names = []
        if isinstance(proxy_res, dict):
            providers = proxy_res.get("providers")
            if isinstance(providers, dict):
                proxy_names = list(providers.keys())

        rule_names = []
        if isinstance(rule_res, dict):
            providers = rule_res.get("providers")
            if isinstance(providers, dict):
                rule_names = list(providers.keys())

        total = len(proxy_names) + len(rule_names)
        if total == 0:
            return {"status": "success", "message": "未检测到任何 Proxy Provider 或 Rule Provider"}, None

        errors = []
        success_count = 0

        # 3. 逐一更新
        for name in proxy_names:
            res, err = self.update_proxy_provider(name)
            if err:
                errors.append(f"代理合集[{name}]: {err}")
            elif isinstance(res, dict) and res.get("status_code") not in (200, 204) and res.get("status") != "success":
                msg = res.get("message") or res.get("body") or f"HTTP {res.get('status_code')}"
                errors.append(f"代理合集[{name}]: {msg}")
            else:
                success_count += 1

        for name in rule_names:
            res, err = self.update_rule_provider(name)
            if err:
                errors.append(f"规则合集[{name}]: {err}")
            elif isinstance(res, dict) and res.get("status_code") not in (200, 204) and res.get("status") != "success":
                msg = res.get("message") or res.get("body") or f"HTTP {res.get('status_code')}"
                errors.append(f"规则合集[{name}]: {msg}")
            else:
                success_count += 1

        if not errors:
            return {
                "status": "success",
                "message": f"共更新 {len(proxy_names)} 个代理合集、{len(rule_names)} 个规则合集",
            }, None

        if success_count == 0:
            return None, f"全部 Provider 更新失败: {'; '.join(errors)}"

        return {
            "status": "warn",
            "message": f"成功更新 {success_count}/{total} 个 Provider，失败：{'; '.join(errors)}",
        }, None

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


DELAY_OK = "ok"
DELAY_TIMEOUT = "timeout"
DELAY_ERROR = "error"


def _delay_error_kind(error) -> str | None:
    """把传输层错误归一化为 timeout / error；无错误返回 None。"""
    if error == ClashAPI.SENT_BUT_DISCONNECTED:
        return DELAY_TIMEOUT
    if not error:
        return None
    low = str(error).lower()
    if "timeout" in low or "timed out" in low or "超时" in low:
        return DELAY_TIMEOUT
    return DELAY_ERROR


def interpret_delay_result(result, error) -> tuple[str, int | None]:
    """解释单节点测速结果：``(DELAY_OK|DELAY_TIMEOUT|DELAY_ERROR, 毫秒或 None)``。

    内核把 504 当正常响应体返回（ClashAPI 不抛异常），因此必须读 ``status_code``。
    """
    kind = _delay_error_kind(error)
    if kind is not None:
        return kind, None
    if not isinstance(result, dict):
        return DELAY_ERROR, None
    status_code = result.get("status_code")
    if status_code == 504:
        return DELAY_TIMEOUT, None
    if status_code in (400, 404, 503):
        return DELAY_ERROR, None
    if status_code != 200:
        return DELAY_ERROR, None
    delay = result.get("delay")
    if isinstance(delay, bool) or not isinstance(delay, int) or delay <= 0:
        return DELAY_ERROR, None
    return DELAY_OK, delay


def interpret_group_delay_result(result, error) -> tuple[str, dict[str, int]]:
    """解释整组测速结果：``(状态, {节点名: 毫秒})``；只有整数项被保留。"""
    kind = _delay_error_kind(error)
    if kind is not None:
        return kind, {}
    if not isinstance(result, dict):
        return DELAY_ERROR, {}
    status_code = result.get("status_code")
    if status_code == 504:
        return DELAY_TIMEOUT, {}
    if status_code in (400, 404, 503):
        return DELAY_ERROR, {}
    if status_code != 200:
        return DELAY_ERROR, {}
    mapping: dict[str, int] = {}
    for key, value in result.items():
        if key == "status_code":
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        mapping[str(key)] = value
    return DELAY_OK, mapping


def _brief(raw, limit: int = 120) -> str:
    """把任意响应体压成一行短文本，供提示栏/通知展示。"""
    if raw is None:
        return "无内容"
    text = " ".join(str(raw).split())
    if not text:
        return "无内容"
    return text[:limit] + "…" if len(text) > limit else text


def _transport_hint(error: str) -> str:
    """把 requests 的传输层错误翻译成人话，认不出来就截断原文。"""
    low = error.lower()
    if "connection refused" in low or "connection aborted" in low:
        return "拒绝连接，请确认 Clash 已启动且控制端口正确"
    if "max retries exceeded" in low:
        return "无法连接到端点，请确认 Clash 已启动且地址端口正确"
    if "timed out" in low:
        return "连接超时，端点可能未响应"
    if "name resolution" in low or "failed to resolve" in low or "nodename" in low:
        return "无法解析主机地址"
    if "ssl" in low or "certificate" in low:
        return "TLS 握手失败"
    if "authorization" in low or "unauthorized" in low:
        return "鉴权失败，请检查 API 密钥"
    return _brief(error)


def interpret_connect_result(result, error) -> tuple[bool, str]:
    """解释连接预检查（GET /version）：返回 ``(能否使用, 版本号或失败原因)``。

    只读探测不存在「写操作已发送但断连」的歧义，所以 ``SENT_BUT_DISCONNECTED``
    在此按连不上处理（写操作仍由 ``interpret_api_result`` 保留原语义）。
    Clash 对密钥错误返回 401 且带响应体，ClashAPI 会把它当正常结果返回，
    因此这里必须校验状态码与版本字段，否则会把鉴权失败误判为连接成功。
    """
    if error == ClashAPI.SENT_BUT_DISCONNECTED:
        return False, "未能读回响应，端点可能未启动或未开放控制接口"
    if error:
        return False, _transport_hint(str(error))
    if not isinstance(result, dict):
        return False, f"端点返回了无法识别的内容：{_brief(result)}"

    status_code = result.get("status_code")
    if isinstance(status_code, int) and not 200 <= status_code < 300:
        if status_code in (401, 403):
            return False, f"鉴权失败（HTTP {status_code}），请检查端点 API 密钥"
        return False, f"HTTP {status_code}：{_brief(result.get('body') or result.get('message'))}"

    version = result.get("version")
    if not version:
        return False, f"响应中没有版本信息，可能不是 Clash 控制接口：{_brief(result)}"
    return True, str(version)

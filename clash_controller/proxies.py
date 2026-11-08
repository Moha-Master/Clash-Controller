"""策略组 / 节点数据归一化（零 Textual 依赖）。

Clash 的 ``GET /proxies`` 把节点与策略组放在同一个顶层字典里：组带 ``all`` 数组，
节点没有；provider 节点只出现在 ``GET /providers/proxies`` 中（``config.go`` 不把
它们注册进顶层字典）。本模块把两份原始响应合并成可展示的 :class:`ProxiesSnapshot`，
并负责把 ``history`` / ``extra`` 里的延迟记录解释成「毫秒 / 失败 / 未测」。

约定：``delay is None`` = 未测；``delay == 0`` = 测试失败（不是 0ms）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_TEST_URL = "https://www.gstatic.com/generate_204"
DEFAULT_TEST_TIMEOUT = 5000

GROUP_TYPE_LABELS = {
    "selector": "手动",
    "urltest": "自动",
    "fallback": "故障转移",
    "loadbalance": "负载均衡",
}


@dataclass
class ProxyNode:
    name: str
    type: str
    udp: bool = False
    provider: str = ""  # 空 = 配置内节点；否则 provider 名
    is_group: bool = False  # 该成员是嵌套策略组
    delay: int | None = None  # None=未测；0=失败；>0=毫秒


@dataclass
class ProxyGroup:
    name: str
    type: str  # 原始类型字符串，如 "Selector"
    kind: str  # 小写归一化：selector/urltest/fallback/loadbalance/other
    now: str = ""
    all: list[str] = field(default_factory=list)
    fixed: str = ""
    hidden: bool = False
    test_url: str = ""


@dataclass
class ProxiesSnapshot:
    nodes: dict[str, ProxyNode]
    groups: dict[str, ProxyGroup]
    order: list[str]  # 组装后的组顺序（GLOBAL 最后）

    def group_list(self, include_hidden: bool = False) -> list[ProxyGroup]:
        out: list[ProxyGroup] = []
        for name in self.order:
            group = self.groups.get(name)
            if group is None:
                continue
            if group.hidden and not include_hidden:
                continue
            out.append(group)
        return out

    def member(self, group: ProxyGroup, name: str) -> ProxyNode:
        node = self.nodes.get(name)
        if node is not None:
            return node
        nested = self.groups.get(name)
        if nested is not None:
            return ProxyNode(
                name=name,
                type=nested.type,
                provider="",
                is_group=True,
                delay=self.resolve_delay(nested.now),
            )
        return ProxyNode(name=name, type="")

    def resolve_delay(self, name: str, _seen: set[str] | None = None) -> int | None:
        """沿组的 ``now`` 递归到叶子节点；遇到环或找不到返回 None。"""
        if not name:
            return None
        seen = _seen if _seen is not None else set()
        if name in seen:
            return None
        seen.add(name)
        node = self.nodes.get(name)
        if node is not None:
            return node.delay
        group = self.groups.get(name)
        if group is None:
            return None
        return self.resolve_delay(group.now, seen)


def group_test_url(group: ProxyGroup) -> str:
    return group.test_url or DEFAULT_TEST_URL


def _as_int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _history_delay(entries) -> int | None:
    if not isinstance(entries, list) or not entries:
        return None
    last = entries[-1]
    if not isinstance(last, dict):
        return None
    return _as_int(last.get("delay"))


def _node_delay(raw: dict) -> int | None:
    """优先取 ``history[-1].delay``；否则取 ``extra`` 里时间最晚的一条记录。"""
    delay = _history_delay(raw.get("history"))
    if delay is not None:
        return delay
    extra = raw.get("extra")
    if not isinstance(extra, dict):
        return None
    best_time: str | None = None
    best: int | None = None
    for entry in extra.values():
        if not isinstance(entry, dict):
            continue
        history = entry.get("history")
        if not isinstance(history, list) or not history:
            continue
        last = history[-1]
        if not isinstance(last, dict):
            continue
        stamp = str(last.get("time") or "")
        value = _as_int(last.get("delay"))
        if value is None:
            continue
        if best_time is None or stamp > best_time:
            best_time = stamp
            best = value
    return best


def _build_node(name: str, raw: dict, provider: str = "") -> ProxyNode:
    return ProxyNode(
        name=name,
        type=str(raw.get("type") or ""),
        udp=bool(raw.get("udp", False)),
        provider=provider,
        is_group=False,
        delay=_node_delay(raw),
    )


def _build_group(name: str, raw: dict) -> ProxyGroup:
    raw_type = str(raw.get("type") or "")
    kind = raw_type.lower()
    if kind not in GROUP_TYPE_LABELS:
        kind = "other"
    members = [str(item) for item in raw.get("all") or [] if isinstance(item, (str, int))]
    return ProxyGroup(
        name=name,
        type=raw_type,
        kind=kind,
        now=str(raw.get("now") or ""),
        all=members,
        fixed=str(raw.get("fixed") or ""),
        hidden=bool(raw.get("hidden", False)),
        test_url=str(raw.get("testUrl") or ""),
    )


def build_snapshot(proxies_result, providers_result=None) -> ProxiesSnapshot:
    """合并 ``/proxies`` 与 ``/providers/proxies`` 的返回体；非法入参按空处理。"""
    nodes: dict[str, ProxyNode] = {}
    groups: dict[str, ProxyGroup] = {}

    data = proxies_result if isinstance(proxies_result, dict) else {}
    raw_proxies = data.get("proxies")
    if isinstance(raw_proxies, dict):
        for key, raw in raw_proxies.items():
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or key)
            if isinstance(raw.get("all"), list):
                groups[name] = _build_group(name, raw)
            else:
                nodes[name] = _build_node(name, raw)

    providers = providers_result.get("providers") if isinstance(providers_result, dict) else None
    if isinstance(providers, dict):
        for key, entry in providers.items():
            if not isinstance(entry, dict):
                continue
            pname = str(entry.get("name") or key)
            # 每个内联组都会生成同名 Compatible provider，default 是内置项，均需过滤
            if pname.lower() == "default":
                continue
            if str(entry.get("vehicleType") or "").lower() == "compatible":
                continue
            plist = entry.get("proxies")
            if not isinstance(plist, list):
                continue
            for raw in plist:
                if not isinstance(raw, dict):
                    continue
                node_name = str(raw.get("name") or "")
                if not node_name:
                    continue
                existing = nodes.get(node_name)
                if existing is not None:
                    if not existing.provider:
                        existing.provider = pname
                    continue
                nodes[node_name] = _build_node(node_name, raw, provider=pname)

    return ProxiesSnapshot(nodes=nodes, groups=groups, order=_group_order(groups))


def _group_order(groups: dict[str, ProxyGroup]) -> list[str]:
    """GLOBAL.all 决定组顺序；不在其中的组按名称排在前面；GLOBAL 永远最后。"""
    global_group = groups.get("GLOBAL")
    if global_group is None:
        return sorted(groups, key=str.casefold)
    listed: list[str] = []
    for name in global_group.all:
        if name != "GLOBAL" and name in groups and name not in listed:
            listed.append(name)
    rest = sorted(
        (name for name in groups if name != "GLOBAL" and name not in listed),
        key=str.casefold,
    )
    return [*rest, *listed, "GLOBAL"]

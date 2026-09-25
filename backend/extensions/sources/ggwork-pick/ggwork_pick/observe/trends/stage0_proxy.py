"""Which proxy a stage 0 session's requests go through, as far as this machine can tell (plan TR-05: the local egress
note; design 4.11). Names and schemes only, never an address: a proxy URL can carry an account.

httpx trusts the environment by default and reads it through urllib.request.getproxies(), which returns the *_proxy
environment variables when any is set and otherwise falls back on the operating system's settings (the macOS network
settings, where Clash or Surge put their system proxy; the Windows registry). This module reads the same two places in
the same order. A TUN-mode VPN routes the traffic below all of this and cannot be seen from here: the report says so.
"""

import os
import sys
import urllib.request
from collections.abc import Callable, Mapping

HTTPX_SCHEMES = ("http", "https", "all")  # the entries httpx mounts a proxy for
SUFFIX = "_proxy"

SystemProxies = Callable[[], Mapping[str, str]]


def system_proxies() -> Mapping[str, str]:
    """The operating system's proxy settings, as urllib reads them when no *_proxy variable is set; a local lookup."""
    if sys.platform == "darwin":
        return urllib.request.getproxies_macosx_sysconf()
    if os.name == "nt":
        return urllib.request.getproxies_registry()
    return {}


def _environment_names(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Every set variable urllib counts as a proxy setting (any name ending in _proxy, any case)."""
    return tuple(sorted(name for name, value in environ.items() if value and name.lower().endswith(SUFFIX)))


def proxy_record(environ: Mapping[str, str], system: SystemProxies = system_proxies) -> dict:
    """What httpx would do with this environment: which proxy variables are set, which bypass variables are set (not a
    proxy), which schemes the system settings proxy when no variable is set, and which of the two is in effect."""
    names = _environment_names(environ)
    proxies = [name for name in names if name.lower()[: -len(SUFFIX)] in HTTPX_SCHEMES]
    bypass = [name for name in names if name.lower()[: -len(SUFFIX)] == "no"]
    schemes = [] if names else sorted(scheme for scheme, value in system().items() if value and scheme in HTTPX_SCHEMES)
    in_effect = "environment" if proxies else ("system" if schemes else None)
    return {"environment": proxies, "no_proxy": bypass, "system": schemes, "in_effect": in_effect}

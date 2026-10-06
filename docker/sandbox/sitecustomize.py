"""Egress guard, loaded by every Python process in the sandbox: name resolution only returns public internet
addresses, so sensor code can't reach the host, its services (the KG API, Ollama, …) or a private network —
not even by IP literal (those resolve through getaddrinfo too). Sensor code may not import socket (static check)."""

import ipaddress
import socket

_getaddrinfo = socket.getaddrinfo


def _public_only(host, *args, **kwargs):
    results = [r for r in _getaddrinfo(host, *args, **kwargs)
               if ipaddress.ip_address(r[4][0].split("%")[0]).is_global]
    if not results:
        raise socket.gaierror(f"{host}: only public internet addresses are reachable from the sandbox")
    return results


socket.getaddrinfo = _public_only

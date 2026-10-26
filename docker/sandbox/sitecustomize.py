"""Egress guard, loaded by every Python process in the sandbox: name resolution only returns public internet
addresses, so sensor code can't reach the host, its services (the KG API, Ollama, …) or a private network —
not even by IP literal (those resolve through getaddrinfo too). Sensor code may not import socket (static check,
runner); a socket that reaches it anyway connects only through this resolution too (socket.connect resolves names
in C, past getaddrinfo above)."""

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

_connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex


def _public_address(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
        info = _public_only(address[0], address[1], sock.family, sock.type)
        return (info[0][4][0], address[1], *address[2:])
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        raise OSError("only public internet addresses are reachable from the sandbox")
    return address


socket.socket.connect = lambda self, address: _connect(self, _public_address(self, address))
socket.socket.connect_ex = lambda self, address: _connect_ex(self, _public_address(self, address))

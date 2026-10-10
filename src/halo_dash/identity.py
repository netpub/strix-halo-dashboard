from __future__ import annotations

import socket
from pathlib import Path

from halo_dash.textio import read_text_file

_HOST_FILES = (
    (Path("/host/hostname"), Path("/host/os-release")),
    (Path("/proc/1/root/etc/hostname"), Path("/proc/1/root/etc/os-release")),
    (Path("/etc/hostname"), Path("/etc/os-release")),
)


def read_host_identity(
    files: tuple[tuple[Path, Path], ...] | None = None,
    *,
    in_container: bool | None = None,
) -> dict[str, str | None]:
    candidates = files if files is not None else _HOST_FILES
    container = Path("/run/.containerenv").is_file() if in_container is None else in_container
    if files is None and container:
        candidates = candidates[:2]
    hostname = None
    os_name = None
    for hostname_path, os_path in candidates:
        if hostname is None:
            hostname = _hostname(hostname_path)
        if os_name is None:
            os_name = _os_name(os_path)
        if hostname and os_name:
            break
    if hostname is None and not container:
        hostname = socket.gethostname() or None
    return {"hostname": hostname, "ip": primary_ipv4(), "os": os_name}


def parse_os_release(text: str) -> str | None:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    pretty = values.get("PRETTY_NAME")
    if pretty:
        return pretty
    name = " ".join(part for part in (values.get("NAME"), values.get("VERSION") or values.get("VERSION_ID")) if part)
    return name or None


def primary_ipv4() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    if not ip or ip.startswith("127."):
        return None
    return ip


def _hostname(path: Path) -> str | None:
    text = read_text_file(path)
    if not text:
        return None
    name = text.splitlines()[0].strip()
    return name or None


def _os_name(path: Path) -> str | None:
    text = read_text_file(path)
    if not text:
        return None
    return parse_os_release(text)

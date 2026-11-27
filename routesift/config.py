"""Application settings and paths anchored to the repository root."""

import os
from ipaddress import IPv4Address, ip_network
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
RESOURCES_DIR = ROOT / "resources"
load_dotenv(ROOT / ".env")
HOST = "127.0.0.1"
PORT = int(os.environ.get("AUDITOR_PORT", "18765"))
BIND_HOST = os.environ.get("AUDITOR_BIND_HOST", "").strip()
if BIND_HOST and BIND_HOST != "0.0.0.0":
    raise RuntimeError("AUDITOR_BIND_HOST 仅支持留空或设为 0.0.0.0")
LAN_IP = os.environ.get("AUDITOR_LAN_IP", "").strip()
LAN_PORT = int(os.environ.get("AUDITOR_LAN_PORT", str(PORT + 1)))
LAN_PASSWORD = os.environ.get("AUDITOR_LAN_PASSWORD", "")
LAN_USER = "routesift"
MODULE_TOKEN = os.environ.get("SHADOWROCKET_MODULE_TOKEN", "").strip()
PRIVATE_NETWORKS = tuple(map(ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))


def lan_host():
    if not LAN_IP:
        return None
    try:
        address = IPv4Address(LAN_IP)
    except ValueError as exc:
        raise RuntimeError("AUDITOR_LAN_IP 必须是本机的局域网 IPv4 地址") from exc
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise RuntimeError("AUDITOR_LAN_IP 必须位于私有 IPv4 网段")
    if not LAN_PASSWORD:
        raise RuntimeError("启用局域网访问时请设置 AUDITOR_LAN_PASSWORD")
    return str(address)


def controller_url():
    value = os.environ.get("MIHOMO_URL", "ws://127.0.0.1:9090/connections")
    try:
        parsed = urlsplit(value)
        parsed.port  # Validate a custom port.
    except ValueError as exc:
        raise RuntimeError("MIHOMO_URL 的端口或地址无效") from exc
    allowed_hosts = {"127.0.0.1", "::1", "localhost"}
    if BIND_HOST:
        allowed_hosts.add("host.docker.internal")
    if (
        parsed.scheme not in {"ws", "wss"}
        or parsed.hostname not in allowed_hosts
        or parsed.path.rstrip("/") != "/connections"
        or parsed.username is not None or parsed.fragment or parsed.query
    ):
        raise RuntimeError("MIHOMO_URL 必须是回环地址上的 WebSocket /connections")
    return value


LAN_HOST = lan_host()
if BIND_HOST and LAN_HOST and LAN_PORT == PORT:
    raise RuntimeError("容器模式下 AUDITOR_LAN_PORT 不能与 AUDITOR_PORT 相同")
MODULE_PATH = (
    f"/rules/ios/{MODULE_TOKEN}/routesift-ios.sgmodule"
    if LAN_HOST and len(MODULE_TOKEN) >= 32 and all(c.isascii() and (c.isalnum() or c in "-_") for c in MODULE_TOKEN)
    else ""
)

DATABASE_PATH = (ROOT / Path(os.environ.get("AUDITOR_DB", "data/traffic.db")).expanduser()).resolve()
MIHOMO_SECRET = os.environ.get("MIHOMO_SECRET", "")
PHONE_LOG_URL = os.environ.get("SHADOWROCKET_LOG_URL", "").strip()
PHONE_LOG_TZ = os.environ.get("SHADOWROCKET_LOG_TZ", "Asia/Singapore")


def deepseek_settings():
    env_file = os.getenv("DEEPSEEK_ENV_FILE", "").strip()
    shared = dotenv_values(Path(env_file).expanduser()) if env_file else {}
    return tuple(
        (os.getenv(name) or shared.get(name) or default).strip()
        for name, default in (
            ("DEEPSEEK_API_KEY", ""), ("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), ("DEEPSEEK_MODEL", "deepseek-flash"),
        )
    )

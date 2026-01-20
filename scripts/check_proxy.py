import asyncio
import json
import time
import socks
import ssl
from pathlib import Path
from country_map import COUNTRY_MAP

BASE = Path(__file__).resolve().parent.parent
PROXY_FILE = BASE / "proxy.txt"
OUT_FILE = BASE / "public" / "proxies.json"

# 超时设置
FAST_TIMEOUT = 10      # 第一阶段：连通性 + 延迟
DEEP_TIMEOUT = 10      # 第二阶段：单个 API 请求

# 深度检测用的 IP API（顺序轮询，命中即停）
TEST_APIS = [
    ("ifconfig.me", 443, "/ip"),
    ("httpbin.org", 443, "/ip"),
    ("api.ipify.org", 443, "/?format=json"),
    ("api.ip.pn", 443, "/json"),
]


def parse_proxy(line: str):
    proto, rest = line.split("//", 1)
    ip, port, country = rest.strip().split(":")
    proto = proto.replace(":", "")
    if proto == "http":
        proto = "https"
    return proto, ip, int(port), country


# ─────────────────────────────────────
# 第一阶段：快速检测（仅测是否能连 + 延迟）
# ─────────────────────────────────────
async def check_latency(ip: str, port: int):
    start = time.time()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=FAST_TIMEOUT
        )
        writer.close()
        await writer.wait_closed()
        return int((time.time() - start) * 1000)
    except Exception:
        return None


# ─────────────────────────────────────
# 第二阶段：深度检测（status-only）
# ─────────────────────────────────────
def deep_check_status_only(proto: str, ip: str, port: int) -> bool:
    """
    通过代理访问多个 IP API
    只要任意一个返回 HTTP 200 即判定可用
    """
    for host, hport, path in TEST_APIS:
        try:
            s = socks.socksocket()

            if proto == "socks4":
                s.set_proxy(socks.SOCKS4, ip, port)
            elif proto == "socks5":
                s.set_proxy(socks.SOCKS5, ip, port)
            else:  # https proxy（CONNECT）
                s.set_proxy(socks.HTTP, ip, port)

            s.settimeout(DEEP_TIMEOUT)
            s.connect((host, hport))

            ctx = ssl.create_default_context()
            ss = ctx.wrap_socket(s, server_hostname=host)

            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: proxy-check\r\n"
                f"Connection: close\r\n\r\n"
            )
            ss.sendall(req.encode())

            data = ss.recv(256)
            ss.close()

            if not data:
                continue

            # 只解析 status line
            status_line = data.split(b"\r\n", 1)[0]
            if b" 200 " in status_line:
                return True

        except Exception:
            continue

    return False


# ─────────────────────────────────────
# 主流程
# ─────────────────────────────────────
async def main():
    # 读取历史数据（用于成功率统计）
    history = {}
    if OUT_FILE.exists():
        with open(OUT_FILE, "r", encoding="utf-8") as f:
            for item in json.load(f):
                history[item["id"]] = item

    results = []
    loop = asyncio.get_event_loop()

    for line in PROXY_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        proto, ip, port, country = parse_proxy(line)
        pid = f"{proto}_{ip}_{port}"

        record = history.get(pid, {
            "id": pid,
            "ip": ip,
            "port": port,
            "protocol": proto,
            "country": country,
            "country_cn": COUNTRY_MAP.get(country, country),
            "success": 0,
            "total": 0,
        })

        # 每次运行都算一次采样
        record["total"] += 1

        # 阶段 1：快速检测
        latency = await check_latency(ip, port)
        if latency is None:
            continue

        # 阶段 2：深度检测（放到线程池，避免阻塞）
        ok = await loop.run_in_executor(
            None, deep_check_status_only, proto, ip, port
        )

        if not ok:
            continue

        # 成功
        record["success"] += 1
        record["latency"] = latency
        record["last_check"] = int(time.time())
        results.append(record)

    OUT_FILE.parent.mkdir(exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())

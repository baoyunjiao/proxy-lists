import asyncio
import json
import time
import socks
from pathlib import Path
from country_map import COUNTRY_MAP

BASE = Path(__file__).resolve().parent.parent
PROXY_FILE = BASE / "proxy.txt"
OUT_FILE = BASE / "public" / "proxies.json"
TIMEOUT = 10


def parse(line):
    proto, rest = line.split("//", 1)
    ip, port, country = rest.strip().split(":")
    proto = proto.replace(":", "")
    if proto == "http":
        proto = "https"
    return proto, ip, int(port), country


async def check_https(ip, port):
    start = time.time()
    try:
        r, w = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=TIMEOUT
        )
        w.close()
        await w.wait_closed()
        return int((time.time() - start) * 1000)
    except Exception:
        return None


def check_socks(ip, port, version):
    start = time.time()
    try:
        s = socks.socksocket()
        s.set_proxy(version, ip, port)
        s.settimeout(TIMEOUT)
        s.connect(("1.1.1.1", 80))
        s.close()
        return int((time.time() - start) * 1000)
    except Exception:
        return None


async def main():
    old = {}
    if OUT_FILE.exists():
        with open(OUT_FILE, "r", encoding="utf-8") as f:
            for i in json.load(f):
                old[i["id"]] = i

    results = []
    tasks = []
    loop = asyncio.get_event_loop()

    for line in PROXY_FILE.read_text().splitlines():
        if not line.strip():
            continue
        proto, ip, port, country = parse(line)
        pid = f"{proto}_{ip}_{port}"

        record = old.get(pid, {
            "id": pid,
            "ip": ip,
            "port": port,
            "protocol": proto,
            "country": country,
            "country_cn": COUNTRY_MAP.get(country, country),
            "success": 0,
            "total": 0,
        })

        if proto == "https":
            coro = check_https(ip, port)
        elif proto == "socks4":
            coro = loop.run_in_executor(None, check_socks, ip, port, socks.SOCKS4)
        else:
            coro = loop.run_in_executor(None, check_socks, ip, port, socks.SOCKS5)

        tasks.append((record, coro))

    for record, coro in tasks:
        record["total"] += 1
        latency = await coro
        if latency is None:
            continue
        record["success"] += 1
        record["latency"] = latency
        record["last_check"] = int(time.time())
        results.append(record)

    OUT_FILE.parent.mkdir(exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())

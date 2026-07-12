from __future__ import annotations

import asyncio
import random
import socket


def find_free_port(min_port: int = 30000, max_port: int = 60000) -> int:
    """Find a free TCP port in the given range."""
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            candidate = random.randint(min_port, max_port)
            sock.bind(("127.0.0.1", candidate))
            return candidate
        except OSError:
            continue
        finally:
            sock.close()


async def wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    """Wait until a TCP port is accepting connections."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=0.5,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.1)
    return False

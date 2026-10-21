"""Actual loopback HTTP five-slot and disconnect behavior, with fake workers."""
import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_five_streams_sixth_rejected_and_disconnect_releases_after_worker():
    root = Path(__file__).resolve().parents[2]
    port = free_port()
    server = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn",
        "stage5_sse_fake_app:app", "--app-dir", str(root / "tests/integration"),
        "--host", "127.0.0.1", "--port", str(port)], cwd=root,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=2, trust_env=False) as http:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    if http.get(url + "/test/stats").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if server.poll() is not None:
                    raise AssertionError("fake SSE app exited")
                time.sleep(.1)
            else:
                raise AssertionError("fake SSE app readiness timeout")

        async def scenario():
            async with httpx.AsyncClient(base_url=url, timeout=20, trust_env=False) as http:
                ready = [asyncio.Event() for _ in range(5)]
                close = [asyncio.Event() for _ in range(5)]

                async def hold(index):
                    async with http.stream("POST", "/qa/sse", json={"query": f"q{index}"}) as response:
                        assert response.status_code == 200
                        async for line in response.aiter_lines():
                            if line == "event: meta":
                                ready[index].set()
                                await close[index].wait()
                                break

                tasks = [asyncio.create_task(hold(i)) for i in range(5)]
                try:
                    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready)), 10)
                    six = await http.post("/qa/sse", json={"query": "sixth"})
                    assert six.status_code == 429
                    close[0].set()
                    await asyncio.wait_for(tasks[0], 5)
                    # A client disconnect cannot preempt the synchronous fake worker.
                    await asyncio.sleep(.5)
                    stats = (await http.get("/test/stats")).json()
                    assert stats["started"] == 5 and stats["availableSlots"] == 0
                    await http.post("/test/release")
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        stats = (await http.get("/test/stats")).json()
                        if stats["exited"] == 5 and stats["availableSlots"] == 5:
                            break
                        await asyncio.sleep(.05)
                    else:
                        raise AssertionError("five workers did not release all slots")
                    return stats
                finally:
                    for event in close:
                        event.set()
                    await asyncio.gather(*tasks, return_exceptions=True)

        stats = asyncio.run(scenario())
        assert stats == {"started": 5, "exited": 5, "availableSlots": 5}
    finally:
        server.terminate()
        server.wait(timeout=10)

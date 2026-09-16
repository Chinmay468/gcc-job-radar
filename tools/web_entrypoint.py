#!/usr/bin/env python3
"""Dual-Mode Web Entrypoint for Render Free Tier Web Service deployment.

Runs:
1. Lightweight HTTP server on a daemon background thread binding to 0.0.0.0:$PORT
   with GET / and GET /health endpoints returning HTTP 200 health check JSON.
2. Long-polling Telegram bot listener on the main asyncio event loop.
3. Graceful signal handling for SIGTERM and SIGINT.
"""

import asyncio
import gc
import http.server
import json
import logging
import os
from pathlib import Path
import signal
import sys
import threading
from typing import Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

# Load environment variables (.env) from project root
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# Ensure standard streams use utf-8 encoding on headless/Windows loggers
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from gcc_job_radar.bot_listener import run_bot_listener
from gcc_job_radar.display import console

logger = logging.getLogger("web_entrypoint")


def get_memory_usage_mb() -> float:
    """Get current process Resident Set Size (RSS) in megabytes."""
    # 1. Linux container /proc/self/status (Render runtime)
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    return round(float(parts[1]) / 1024.0, 2)
    except Exception:
        pass

    # 2. Unix / macOS resource module
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return round(rusage / (1024.0 * 1024.0), 2)
        return round(rusage / 1024.0, 2)
    except Exception:
        pass

    # 3. Tracemalloc fallback
    try:
        import tracemalloc
        if tracemalloc.is_tracing():
            cur, _ = tracemalloc.get_traced_memory()
            return round(cur / (1024.0 * 1024.0), 2)
    except Exception:
        pass

    return 0.0


class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler serving health checks for Render keep-alive monitors."""

    def _send_response_payload(self, status: int, data: dict, write_body: bool = True) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        if write_body:
            self.wfile.write(payload)

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path in ("", "/health"):
            payload = {
                "status": "healthy",
                "service": "gcc-job-radar",
                "memory_rss_mb": get_memory_usage_mb(),
            }
            self._send_response_payload(200, payload)
        else:
            self._send_response_payload(404, {"error": "not found"})

    def do_HEAD(self) -> None:
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path in ("", "/health"):
            self._send_response_payload(200, {"status": "healthy", "service": "gcc-job-radar"}, write_body=False)
        else:
            self._send_response_payload(404, {"error": "not found"}, write_body=False)

    def log_message(self, format: str, *args: object) -> None:
        """Suppress default stderr HTTP request logging to keep console logs clean."""
        return


def start_http_server(host: str = "0.0.0.0", port: int = 10000) -> http.server.HTTPServer:
    """Start the HTTP server on a daemon background thread."""
    server = http.server.HTTPServer((host, port), HealthCheckHandler)
    server_thread = threading.Thread(
        target=server.serve_forever,
        name="render-health-server",
        daemon=True,
    )
    server_thread.start()
    logger.info("HTTP Health Check server listening on http://%s:%d (GET / and GET /health)", host, port)
    try:
        console.print(f"[bold green]✔ HTTP Health Check Server running at http://{host}:{port}/health[/bold green]")
    except Exception:
        pass
    return server


async def memory_watchdog(interval_seconds: int = 300) -> None:
    """Periodic memory maintenance and logging to keep RAM footprint low."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            rss_mb = get_memory_usage_mb()
            collected = gc.collect()
            new_rss = get_memory_usage_mb()
            if rss_mb > 250:
                logger.warning(
                    "Memory watchdog: High RAM usage detected (%.2f MB). GC collected %d objects -> %.2f MB",
                    rss_mb,
                    collected,
                    new_rss,
                )
            else:
                logger.debug("Memory watchdog: RAM usage %.2f MB (GC reclaimed %d objects)", new_rss, collected)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("Memory watchdog error: %s", exc)


async def run_services() -> None:
    """Run HTTP health server and Telegram bot listener with graceful shutdown."""
    port = int(os.getenv("PORT", "10000"))
    server = start_http_server(host="0.0.0.0", port=port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        logger.info("Shutdown signal received, initiating graceful termination...")
        loop.call_soon_threadsafe(stop_event.set)

    # Register OS signal handlers
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _on_signal)
            except (NotImplementedError, RuntimeError):
                # Fallback for Windows or non-main threads
                try:
                    signal.signal(sig, lambda s, f: _on_signal())
                except Exception:
                    pass

    watchdog_task = asyncio.create_task(memory_watchdog())
    bot_task = asyncio.create_task(run_bot_listener())
    stop_waiter = asyncio.create_task(stop_event.wait())

    try:
        done, pending = await asyncio.wait(
            [bot_task, stop_waiter],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_waiter in done:
            logger.info("Termination requested. Cancelling bot listener...")
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass
        elif bot_task in done:
            exc = bot_task.exception()
            if exc:
                logger.error("Bot listener exited with error: %s", exc)
                raise exc
            logger.info("Bot listener finished cleanly.")

    finally:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
        logger.info("Shutting down HTTP server...")
        server.shutdown()
        server.server_close()
        logger.info("Services stopped cleanly.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting GCC Job Radar Dual-Mode Web Entrypoint...")
    try:
        asyncio.run(run_services())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application stopped.")
    except Exception as exc:
        logger.exception("Fatal exception in main entrypoint: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

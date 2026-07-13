"""Launchers. `serve()` runs the API + opens the browser; `desktop()` wraps it in a
native window (app feel) via pywebview when available."""
from __future__ import annotations

import threading
import webbrowser

import uvicorn

from .config import settings


def serve(open_browser: bool = True) -> None:
    url = f"http://{settings.host}:{settings.port}"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Ledgerline on {url}  (auth {'on' if settings.passphrase else 'OFF - dev'})")
    uvicorn.run("ledgerline.web.app:app", host=settings.host, port=settings.port, log_level="info")


def desktop() -> None:
    """Native-window app feel. Runs the server in a background thread and opens a
    pywebview window. Falls back to browser mode if pywebview isn't installed."""
    try:
        import webview  # pywebview
    except ImportError:
        print("pywebview not installed; falling back to browser mode. `pip install pywebview`.")
        return serve(open_browser=True)

    def _run():
        uvicorn.run("ledgerline.web.app:app", host=settings.host, port=settings.port, log_level="warning")

    threading.Thread(target=_run, daemon=True).start()
    webview.create_window("Ledgerline", f"http://{settings.host}:{settings.port}", width=1200, height=820)
    webview.start()

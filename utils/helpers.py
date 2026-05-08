"""
utils/helpers.py — Shared utilities: performance tracking, logging, retries, rate limiting.
"""

import functools
import logging
import threading
import time
from typing import Any, Callable, Optional

import requests


# ---------------------------------------------------------------------------
# Logging Configuration — Streamlit-safe
# ---------------------------------------------------------------------------
# Why not basicConfig: Streamlit attaches its own handlers to the root logger
# before user code runs, so basicConfig is a no-op and the file handler never
# attaches. We target the named "ott_lead_gen" logger directly instead.
#
# Why propagate=False: without it, every log record travels up to Streamlit's
# root handler AND our handler — producing duplicate lines in the console.

def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure the ott_lead_gen logger with console + file handlers.
    Safe to call multiple times — re-calling with a new level updates it.
    Works correctly under Streamlit (does not rely on basicConfig).
    """
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  —  %(message)s"
    pipeline_logger = logging.getLogger("ott_lead_gen")
    pipeline_logger.setLevel(level)

    # Only attach handlers once — but always update the level
    if not pipeline_logger.handlers:
        formatter = logging.Formatter(fmt)

        c_handler = logging.StreamHandler()
        c_handler.setFormatter(formatter)
        pipeline_logger.addHandler(c_handler)

        try:
            f_handler = logging.FileHandler("ott_lead_gen.log", encoding="utf-8")
            f_handler.setFormatter(formatter)
            pipeline_logger.addHandler(f_handler)
        except Exception as exc:
            print(f"[WARNING] Could not create log file: {exc}")

    else:
        # Handlers already exist — just update the level (handles --debug re-call)
        pipeline_logger.setLevel(level)
        for h in pipeline_logger.handlers:
            h.setLevel(level)

    # Stop records propagating to Streamlit's root logger (prevents double-printing)
    pipeline_logger.propagate = False

    # Silence noisy third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("gspread").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Performance Tracking
# ---------------------------------------------------------------------------

def track_performance(module_name: str) -> Callable:
    """
    Decorator: logs START, SUCCESS/FAILURE, and duration for a tool call.
    Used on Apollo and Exa enrichment steps.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            perf_logger = logging.getLogger(f"ott_lead_gen.{module_name}")
            start_time = time.perf_counter()
            perf_logger.info(f"START: {func.__name__}")
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start_time
                perf_logger.info(f"SUCCESS: {func.__name__} in {elapsed:.2f}s")
                return result
            except Exception as exc:
                elapsed = time.perf_counter() - start_time
                perf_logger.error(f"FAILURE: {func.__name__} after {elapsed:.2f}s: {exc}")
                raise
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Retry Decorator
# ---------------------------------------------------------------------------

def with_retries(
    max_attempts: int = 3,
    delay: float = 5.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Retry on specified exceptions with exponential back-off."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_delay = delay
            _log = logging.getLogger("ott_lead_gen")
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_attempts:
                        _log.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {exc}"
                        )
                        raise
                    _log.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed "
                        f"({type(exc).__name__}: {exc}). Retrying in {current_delay:.1f}s…"
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Token-bucket rate limiter. Thread-safe."""

    def __init__(self, requests_per_minute: int) -> None:
        self._min_interval = 60.0 / max(1, requests_per_minute)
        self._lock = threading.Lock()
        self._last_call: float = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


# ---------------------------------------------------------------------------
# Jina Reader
# ---------------------------------------------------------------------------

def jina_read(url: str, api_key: str = "", timeout: int = 30) -> str:
    """
    Fetch clean markdown text from any URL via Jina Reader API.
    Handles SPAs, LinkedIn posts (if public), and PDF documents.
    """
    _log = logging.getLogger("ott_lead_gen.jina")
    try:
        headers = {"Accept": "text/plain"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = requests.get(
            f"https://r.jina.ai/{url}",
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.text.strip()
        _log.debug(f"Jina read {url}: {len(text)} chars")
        return text
    except Exception as exc:
        _log.warning(f"Jina read failed for {url}: {exc}")
        return ""

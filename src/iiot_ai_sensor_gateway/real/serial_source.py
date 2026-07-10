from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any


class SerialDependencyError(RuntimeError):
    """Raised when serial mode is requested but pyserial is unavailable."""


class SerialLineSource:
    """Read newline-delimited payloads with bounded idle and reconnect backoff."""

    source_name = "lora_serial"

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: float = 1.0,
        *,
        idle_sleep_sec: float = 0.05,
        reconnect_initial_sec: float = 0.5,
        reconnect_max_sec: float = 30.0,
        serial_factory: Callable[..., Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_reconnect_attempts: int = 0,
    ) -> None:
        if not port:
            raise ValueError("serial port must not be empty")
        if baudrate <= 0 or timeout <= 0:
            raise ValueError("baudrate and timeout must be positive")
        if idle_sleep_sec <= 0 or reconnect_initial_sec <= 0 or reconnect_max_sec <= 0:
            raise ValueError("serial sleep/backoff values must be positive")
        if reconnect_initial_sec > reconnect_max_sec:
            raise ValueError("initial reconnect delay must not exceed maximum delay")
        if max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts must be >= 0")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.idle_sleep_sec = idle_sleep_sec
        self.reconnect_initial_sec = reconnect_initial_sec
        self.reconnect_max_sec = reconnect_max_sec
        self.serial_factory = serial_factory
        self.sleep_fn = sleep_fn
        self.max_reconnect_attempts = max_reconnect_attempts

    def metadata(self) -> dict[str, str | int | float]:
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "timeout": self.timeout,
            "idle_sleep_sec": self.idle_sleep_sec,
            "reconnect_initial_sec": self.reconnect_initial_sec,
            "reconnect_max_sec": self.reconnect_max_sec,
        }

    def _factory_and_errors(self) -> tuple[Callable[..., Any], tuple[type[BaseException], ...]]:
        if self.serial_factory is not None:
            return self.serial_factory, (OSError, RuntimeError)
        try:
            import serial  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise SerialDependencyError(
                "Serial mode requires pyserial. Install command: python -m pip install '.[serial]'"
            ) from exc
        return serial.Serial, (serial.SerialException, OSError)

    def iter_lines(self) -> Iterator[str]:
        factory, reconnect_errors = self._factory_and_errors()
        reconnect_delay = self.reconnect_initial_sec
        failed_attempts = 0
        while True:
            connection = None
            try:
                connection = factory(self.port, self.baudrate, timeout=self.timeout)
                failed_attempts = 0
                reconnect_delay = self.reconnect_initial_sec
                while True:
                    raw = connection.readline()
                    if not raw:
                        # A blocking timeout may return immediately on some fake or
                        # misconfigured transports. This explicit sleep bounds CPU.
                        self.sleep_fn(self.idle_sleep_sec)
                        continue
                    if isinstance(raw, str):
                        line = raw
                    else:
                        line = bytes(raw).decode("utf-8", errors="replace")
                    yield line.rstrip("\r\n")
            except reconnect_errors:
                failed_attempts += 1
                if self.max_reconnect_attempts and failed_attempts > self.max_reconnect_attempts:
                    raise
                self.sleep_fn(reconnect_delay)
                reconnect_delay = min(self.reconnect_max_sec, reconnect_delay * 2)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

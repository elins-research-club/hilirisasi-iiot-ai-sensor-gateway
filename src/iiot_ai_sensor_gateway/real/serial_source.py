from __future__ import annotations

from typing import Iterator


class SerialDependencyError(RuntimeError):
    """Raised when serial mode is requested but pyserial is unavailable."""


class SerialLineSource:
    """Read newline-delimited payloads from a serial UART port."""

    source_name = "lora_serial"

    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

    def metadata(self) -> dict[str, str | int | float]:
        return {"port": self.port, "baudrate": self.baudrate, "timeout": self.timeout}

    def iter_lines(self) -> Iterator[str]:
        try:
            import serial  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise SerialDependencyError(
                "Serial mode requires pyserial. Install it with: py -3.13 -m pip install pyserial"
            ) from exc

        with serial.Serial(self.port, self.baudrate, timeout=self.timeout) as connection:
            while True:
                raw = connection.readline()
                if not raw:
                    continue
                yield raw.decode("utf-8", errors="replace").rstrip("\r\n")

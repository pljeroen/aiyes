"""Tests for AtSpiWorkerConnection — parent-side connection manager.

Uses the mock persistent worker fixture for testing.
"""

from __future__ import annotations

import os
import threading
import time

import pytest


def _mock_worker_path() -> str:
    """Return absolute path to the mock worker script."""
    return os.path.join(
        os.path.dirname(__file__),
        "fixtures",
        "mock_persistent_worker.py",
    )


@pytest.fixture()
def connection():
    """Create and start a worker connection using the mock worker."""
    from aiyes.adapters.atspi_worker_connection import AtSpiWorkerConnection

    conn = AtSpiWorkerConnection(
        display=":99",
        bus_address="unix:path=/tmp/test-dbus",
        worker_script=_mock_worker_path(),
    )
    conn.start()
    yield conn
    try:
        conn.stop()
    except Exception:
        pass


class TestConnectionStartStop:
    """Start sends startup, receives handshake. Stop sends shutdown."""

    def test_connection_start_stop(self) -> None:
        from aiyes.adapters.atspi_worker_connection import AtSpiWorkerConnection

        conn = AtSpiWorkerConnection(
            display=":99",
            bus_address="unix:path=/tmp/test-dbus",
            worker_script=_mock_worker_path(),
        )
        conn.start()
        assert conn.is_alive()
        conn.stop()
        assert not conn.is_alive()


class TestConnectionSendReceive:
    """send() writes request, reads response, returns result."""

    def test_connection_send_ping(self, connection) -> None:
        result = connection.send("ping")
        assert result == {"status": "alive"}

    def test_connection_send_get_tree(self, connection) -> None:
        result = connection.send("get_tree")
        assert "tree" in result
        assert "registry" in result

    def test_connection_send_do_action(self, connection) -> None:
        result = connection.send(
            "do_action",
            node_id="n_001",
            action_name="click",
        )
        assert result["success"] is True

    def test_connection_send_list_windows(self, connection) -> None:
        result = connection.send("list_windows")
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0]["name"] == "MockApp"


class TestConnectionIsAlive:
    """Returns True when process is running, False when dead."""

    def test_connection_is_alive_when_running(self, connection) -> None:
        assert connection.is_alive()

    def test_connection_is_alive_after_stop(self) -> None:
        from aiyes.adapters.atspi_worker_connection import AtSpiWorkerConnection

        conn = AtSpiWorkerConnection(
            display=":99",
            bus_address="unix:path=/tmp/test-dbus",
            worker_script=_mock_worker_path(),
        )
        conn.start()
        conn.stop()
        assert not conn.is_alive()

    def test_connection_is_alive_before_start(self) -> None:
        from aiyes.adapters.atspi_worker_connection import AtSpiWorkerConnection

        conn = AtSpiWorkerConnection(
            display=":99",
            bus_address="unix:path=/tmp/test-dbus",
            worker_script=_mock_worker_path(),
        )
        assert not conn.is_alive()


class TestConnectionSendToDeadWorker:
    """Raises RuntimeError when worker is dead."""

    def test_connection_send_to_dead_worker(self) -> None:
        from aiyes.adapters.atspi_worker_connection import AtSpiWorkerConnection

        conn = AtSpiWorkerConnection(
            display=":99",
            bus_address="unix:path=/tmp/test-dbus",
            worker_script=_mock_worker_path(),
        )
        conn.start()
        conn.stop()
        with pytest.raises(RuntimeError, match="[Ww]orker"):
            conn.send("ping")


class TestConnectionThreadSafety:
    """Concurrent sends are serialized (no interleaving)."""

    def test_connection_thread_safety(self, connection) -> None:
        results = []
        errors = []

        def send_ping(idx: int) -> None:
            try:
                result = connection.send("ping")
                results.append((idx, result))
            except Exception as exc:
                errors.append((idx, exc))

        threads = [threading.Thread(target=send_ping, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 10
        for _, result in results:
            assert result == {"status": "alive"}


class TestConnectionTimeout:
    """send() raises TimeoutError after configured timeout."""

    def test_connection_timeout(self) -> None:
        from aiyes.adapters.atspi_worker_connection import AtSpiWorkerConnection

        # Use mock worker with 5s delay, but 0.5s timeout
        conn = AtSpiWorkerConnection(
            display=":99",
            bus_address="unix:path=/tmp/test-dbus",
            worker_script=_mock_worker_path(),
            command_timeout=0.5,
        )
        conn.start()
        try:
            # The mock worker has --delay support
            conn._worker_extra_args = ["--delay", "5"]
            # Restart with delay args
            conn.stop()
            conn._worker_extra_args = ["--delay", "5"]
            conn = AtSpiWorkerConnection(
                display=":99",
                bus_address="unix:path=/tmp/test-dbus",
                worker_script=_mock_worker_path(),
                command_timeout=0.5,
                worker_extra_args=["--delay", "5"],
            )
            conn.start()
            with pytest.raises(TimeoutError):
                conn.send("ping")
        finally:
            try:
                conn.stop()
            except Exception:
                pass


class TestConnectionStartupTimeout:
    """start() raises RuntimeError if handshake times out."""

    def test_connection_startup_timeout(self) -> None:
        from aiyes.adapters.atspi_worker_connection import AtSpiWorkerConnection

        conn = AtSpiWorkerConnection(
            display=":99",
            bus_address="unix:path=/tmp/test-dbus",
            worker_script=_mock_worker_path(),
            startup_timeout=0.5,
            worker_extra_args=["--slow-handshake", "10"],
        )
        with pytest.raises(RuntimeError, match="[Ff]ailed to start|[Hh]andshake"):
            conn.start()

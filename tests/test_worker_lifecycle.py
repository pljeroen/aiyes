"""Tests for persistent worker session lifecycle integration.

Verifies that the composition root correctly wires worker lifecycle
to session start/stop, and that worker failure never fails session operations.

See CONTRACT.md section 12.7.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aiyes.domain.session import Session


# ─── Helpers ──────────────────────────────────────────────────────────


def _make_linux_session() -> Session:
    """Create a minimal Linux session."""
    return Session(
        session_id="test-001",
        app_pid=12345,
        app_command="gedit",
        app_args=(),
        name=None,
        display=":99",
        atspi_bus_pid=12346,
        atspi_bus_address="unix:abstract=/tmp/dbus-test",
        xvfb_pid=12344,
        backend="linux",
    )


def _make_android_session() -> Session:
    """Create a minimal Android session."""
    return Session(
        session_id="test-002",
        app_pid=0,
        app_command="com.example.app",
        app_args=(),
        name=None,
        backend="android",
        device_serial="emulator-5554",
    )


# ─── Lifecycle tests ─────────────────────────────────────────────────


class TestStartWorkerForSession:
    """start_worker_for_session() in composition_root."""

    @patch("aiyes.cli.composition_root.AtSpiWorkerConnection")
    def test_start_worker_for_linux_session(self, mock_conn_cls: MagicMock) -> None:
        """start_worker_for_session creates and injects worker for linux."""
        from aiyes.cli import composition_root

        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        session = _make_linux_session()

        # Reset module state
        composition_root._active_worker = None

        composition_root.start_worker_for_session(session)

        mock_conn_cls.assert_called_once_with(
            session.display, session.atspi_bus_address
        )
        mock_conn.start.assert_called_once()
        assert composition_root._active_worker is mock_conn

    @patch("aiyes.cli.composition_root.AtSpiWorkerConnection")
    def test_start_worker_skips_android_session(self, mock_conn_cls: MagicMock) -> None:
        """start_worker_for_session is a no-op for android."""
        from aiyes.cli import composition_root

        session = _make_android_session()

        composition_root._active_worker = None

        composition_root.start_worker_for_session(session)

        mock_conn_cls.assert_not_called()
        assert composition_root._active_worker is None

    @patch("aiyes.cli.composition_root.AtSpiWorkerConnection")
    def test_start_worker_catches_startup_failure(
        self, mock_conn_cls: MagicMock
    ) -> None:
        """Worker startup failure is caught; _active_worker stays None."""
        from aiyes.cli import composition_root

        mock_conn = MagicMock()
        mock_conn.start.side_effect = RuntimeError("gi not available")
        mock_conn_cls.return_value = mock_conn

        session = _make_linux_session()
        composition_root._active_worker = None

        # Must not raise
        composition_root.start_worker_for_session(session)

        assert composition_root._active_worker is None


class TestStopWorker:
    """stop_worker() in composition_root."""

    def test_stop_worker_cleans_up(self) -> None:
        """stop_worker clears worker from adapters and stops process."""
        from aiyes.cli import composition_root

        mock_worker = MagicMock()
        composition_root._active_worker = mock_worker

        # Inject worker into adapters so we can verify cleanup
        composition_root._atspi_tree.set_worker(mock_worker)
        composition_root._atspi_action.set_worker(mock_worker)
        composition_root._atspi_window.set_worker(mock_worker)

        composition_root.stop_worker()

        mock_worker.stop.assert_called_once()
        assert composition_root._active_worker is None
        # Adapters should have worker cleared
        assert composition_root._atspi_tree._worker is None
        assert composition_root._atspi_action._worker is None
        assert composition_root._atspi_window._worker is None

    def test_stop_worker_idempotent(self) -> None:
        """stop_worker when no worker is active is a no-op."""
        from aiyes.cli import composition_root

        composition_root._active_worker = None

        # Must not raise
        composition_root.stop_worker()

        assert composition_root._active_worker is None

    def test_stop_worker_catches_stop_exception(self) -> None:
        """stop_worker catches exceptions from worker.stop()."""
        from aiyes.cli import composition_root

        mock_worker = MagicMock()
        mock_worker.stop.side_effect = OSError("broken pipe")
        composition_root._active_worker = mock_worker

        # Must not raise
        composition_root.stop_worker()

        assert composition_root._active_worker is None

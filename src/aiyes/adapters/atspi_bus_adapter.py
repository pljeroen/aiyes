"""AtSpiBusAdapter — implements AccessibilityBusPort via subprocess.

Creates an isolated D-Bus session bus via dbus-daemon, then starts
at-spi-bus-launcher and at-spi2-registryd on that bus. Sets
IsEnabled=true and, where advertised, ScreenReaderEnabled=true for
legacy AccessKit activation. Returns the real session bus address.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import select
import signal
import stat
import tempfile
from contextlib import contextmanager
import shutil
import subprocess
import time
from typing import Dict, List
from xml.etree import ElementTree

from aiyes.domain.types import BusStartResult

_FALLBACK_LAUNCHER_PATH = "/usr/libexec/at-spi-bus-launcher"
_FALLBACK_REGISTRYD_PATH = "/usr/libexec/at-spi2-registryd"


def _resolve_launcher() -> str:
    """Resolve the at-spi-bus-launcher executable.

    Uses shutil.which to find it in PATH first, falls back to
    the well-known /usr/libexec/ location.
    """
    found = shutil.which("at-spi-bus-launcher")
    if found is not None:
        return found
    return _FALLBACK_LAUNCHER_PATH


def _resolve_registryd() -> str:
    """Resolve the at-spi2-registryd executable.

    Uses shutil.which to find it in PATH first, falls back to
    the well-known /usr/libexec/ location.
    """
    found = shutil.which("at-spi2-registryd")
    if found is not None:
        return found
    return _FALLBACK_REGISTRYD_PATH


def _has_screen_reader_property(session_bus_address: str) -> bool:
    """Read status metadata; failed or malformed discovery cannot permit startup."""
    result = subprocess.run(
        [
            "dbus-send",
            f"--bus={session_bus_address}",
            "--dest=org.a11y.Bus",
            "--print-reply",
            "/org/a11y/bus",
            "org.freedesktop.DBus.Introspectable.Introspect",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    header, separator, body = result.stdout.partition("\n")
    body = body.strip()
    if (
        not header.startswith("method return ")
        or not separator
        or not body.startswith('string "')
        or not body.endswith('"')
    ):
        raise RuntimeError("Invalid AT-SPI status introspection reply")
    try:
        node = ElementTree.fromstring(body[len('string "') : -1])
    except ElementTree.ParseError as error:
        raise RuntimeError("Invalid AT-SPI status introspection XML") from error
    interfaces = node.findall("interface[@name='org.a11y.Status']")
    if node.tag != "node" or len(interfaces) != 1:
        raise RuntimeError("AT-SPI introspection must expose org.a11y.Status")
    properties = {}
    for prop in interfaces[0].findall("property"):
        name = prop.get("name")
        if name in properties:
            raise RuntimeError("Duplicate AT-SPI status property")
        properties[name] = prop
    for name in ("IsEnabled", "ScreenReaderEnabled"):
        prop = properties.get(name)
        if prop is None:
            if name == "IsEnabled":
                raise RuntimeError("AT-SPI status is missing IsEnabled")
        elif prop.get("type") != "b" or prop.get("access") != "readwrite":
            raise RuntimeError(f"AT-SPI status property {name} is not a writable boolean")
    return "ScreenReaderEnabled" in properties


def _require_pidfds() -> None:
    if not callable(getattr(os, "pidfd_open", None)) or not callable(
        getattr(signal, "pidfd_send_signal", None)
    ):
        raise RuntimeError("Safe AT-SPI cleanup requires Linux pidfd support")


def _boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _identity(pid: int) -> dict:
    root = Path(f"/proc/{pid}")
    fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
    state = fields[0]
    if state == "Z":
        return {"pid": pid, "birth": fields[19], "state": state}
    environment = {}
    for value in (root / "environ").read_bytes().split(b"\0"):
        key, separator, item = value.partition(b"=")
        if separator and key in (b"HOME", b"DISPLAY", b"DBUS_SESSION_BUS_ADDRESS"):
            environment[key.decode()] = item.decode()
    return {
        "pid": pid, "birth": fields[19], "state": state,
        "ppid": int(fields[1]), "uid": root.stat().st_uid,
        "exe": os.readlink(root / "exe"), "environment": environment,
    }


def _pin_identity(pid: int) -> tuple[int, dict]:
    try:
        descriptor = os.pidfd_open(pid, 0)
    except ProcessLookupError:
        raise
    except OSError as error:
        raise RuntimeError(f"Cannot pin AT-SPI process identity: {error}") from error
    try:
        return descriptor, _identity(pid)
    except BaseException:
        os.close(descriptor)
        raise


def _acquire_child(proc: subprocess.Popen, environment: dict) -> tuple[int, dict]:
    """Wait at most one second for the pinned child's post-exec environment."""
    descriptor, observed = _pin_identity(proc.pid)
    birth = observed["birth"]
    expected = {key: value for key, value in environment.items() if key in ("HOME", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS")}
    deadline = time.monotonic() + 1.0
    try:
        while True:
            if observed["birth"] != birth or observed["state"] == "Z":
                raise RuntimeError("AT-SPI child exited before ownership context was established")
            if all(observed["environment"].get(key) == value for key, value in expected.items()):
                return descriptor, observed
            if time.monotonic() >= deadline:
                raise RuntimeError("AT-SPI child did not expose its expected isolated context")
            select.select([], [], [], 0.01)
            observed = _identity(proc.pid)
    except BaseException:
        os.close(descriptor)
        raise


def _private_directory(create: bool) -> Path:
    parent = Path.home() / ".aieyes"
    root = parent / ".atspi-ownership"
    boot = root / _boot_id()
    for directory in (parent, root, boot):
        if create:
            directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError("Unsafe AT-SPI ownership directory")
        if directory != parent and stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError("AT-SPI ownership directory must be private")
    return boot


def _check_file(path: Path, descriptor: int) -> None:
    info = path.stat(follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise RuntimeError("Unsafe AT-SPI ownership metadata")


@contextmanager
def _locked_receipt(pid: int, create: bool = False):
    directory = _private_directory(create)
    path = directory / f"{pid}.json"
    lock = directory / f"{pid}.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT if create else 0), 0o600)
    handle = os.fdopen(descriptor, "r+b")
    try:
        _check_file(lock, descriptor)
        deadline = time.monotonic() + 3.0
        acquired = False
        while not acquired:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("AT-SPI ownership lock timed out")
                select.select([], [], [], 0.01)
        yield path
    finally:
        handle.close()


def _read_receipt(path: Path, pid: int) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "r") as handle:
        _check_file(path, descriptor)
        data = json.load(handle)
    if (
        not isinstance(data, dict) or data.get("version") != 1
        or data.get("boot") != _boot_id() or data.get("uid") != os.getuid()
        or data.get("pid") != pid or type(pid) is not int or pid <= 0
        or data.get("status") not in ("active", "retired")
        or not isinstance(data.get("bus"), str) or not data["bus"].startswith("unix:")
        or not isinstance(data.get("display"), str)
        or data.get("home") != str(Path.home())
    ):
        raise RuntimeError("Invalid or stale AT-SPI ownership receipt")
    if data["status"] == "retired":
        if "targets" in data:
            raise RuntimeError("Ambiguous retired AT-SPI ownership receipt")
        return data
    targets = data.get("targets")
    if not isinstance(targets, list) or len(targets) < 3:
        raise RuntimeError("Incomplete AT-SPI ownership receipt")
    pids = set()
    for item in targets:
        if (
            not isinstance(item, dict) or type(item.get("pid")) is not int
            or item["pid"] <= 0 or item["pid"] in pids
            or not isinstance(item.get("birth"), str) or not item["birth"].isdigit()
            or item.get("uid") != os.getuid() or not isinstance(item.get("exe"), str)
            or not isinstance(item.get("environment"), dict)
        ):
            raise RuntimeError("Invalid AT-SPI target identity")
        pids.add(item["pid"])
    if [t.get("role") for t in targets[:3]] != ["session", "launcher", "registry"] or targets[0]["pid"] != pid:
        raise RuntimeError("Invalid AT-SPI ownership roots")
    for target in targets:
        env = target["environment"]
        if env.get("HOME") != data["home"]:
            raise RuntimeError("Foreign AT-SPI ownership context")
        if target["role"] in ("launcher", "registry") and env.get("DBUS_SESSION_BUS_ADDRESS") != data["bus"]:
            raise RuntimeError("Foreign AT-SPI bus context")
        if target["role"] == "launcher" and env.get("DISPLAY") != data["display"]:
            raise RuntimeError("Foreign AT-SPI display context")
    return data


def _publish_receipt(path: Path, data: dict, exclusive: bool = False) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".ownership-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(data, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            os.link(temporary, path, follow_symlinks=False)
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _validate_target(target: dict, observed: dict, direct: bool = False) -> None:
    if observed["birth"] != target["birth"]:
        raise RuntimeError("AT-SPI PID was reused; refusing cleanup")
    if observed["state"] == "Z":
        return
    for field in (("uid", "exe") if direct else ("uid", "exe", "environment")):
        if observed[field] != target[field]:
            raise RuntimeError("AT-SPI process identity/context changed; refusing cleanup")


def _recover_targets(data: dict, descriptors: dict, direct: set) -> None:
    for target in data["targets"]:
        try:
            descriptor, observed = _pin_identity(target["pid"])
        except ProcessLookupError:
            continue
        except FileNotFoundError:
            continue
        descriptors[target["pid"]] = descriptor
        _validate_target(target, observed, target["pid"] in direct)


def _inventory(data: dict, descriptors: dict) -> None:
    """One /proc snapshot and finite ancestry closure, with a three-second deadline."""
    deadline = time.monotonic() + 3.0
    parents = {}
    for entry in Path("/proc").iterdir():
        if time.monotonic() >= deadline:
            raise RuntimeError("AT-SPI process inventory timed out")
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        parents[int(entry.name)] = int(fields[1])
    known = {item["pid"] for item in data["targets"]}
    # Only ancestry from already pinned/validated live targets establishes ownership.
    live = {
        target["pid"] for target in data["targets"]
        if target["pid"] in descriptors and not _exited(descriptors[target["pid"]], target, probe_fd=False)
    }
    while live:
        if time.monotonic() >= deadline:
            raise RuntimeError("AT-SPI process inventory timed out")
        children = {pid for pid, parent in parents.items() if parent in live and pid not in known}
        if not children:
            break
        next_live = set()
        for pid in sorted(children):
            try:
                descriptor, observed = _pin_identity(pid)
            except (FileNotFoundError, ProcessLookupError):
                continue
            descriptors[pid] = descriptor
            if observed["state"] == "Z":
                continue
            environment = observed["environment"]
            if (
                observed["ppid"] not in live or observed["uid"] != data["uid"]
                or environment.get("HOME") != data["home"]
                or environment.get("DBUS_SESSION_BUS_ADDRESS") not in (data["bus"], data.get("atspi_bus"))
            ):
                raise RuntimeError("Uncertain AT-SPI descendant ownership")
            observed["role"] = "descendant"
            data["targets"].append(observed)
            known.add(pid)
            next_live.add(pid)
        live = next_live


def _exited(descriptor: int, target: dict, probe_fd: bool = True) -> bool:
    # A zombie cannot execute; it may belong to another parent's wait responsibility.
    try:
        fields = Path(f"/proc/{target['pid']}/stat").read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return True
    if fields[19] != target["birth"]:
        return True  # The pinned identity exited; a replacement is never signalled.
    if fields[0] == "Z":
        return True
    if not probe_fd:
        return False
    poller = select.poll()
    poller.register(descriptor, select.POLLIN)
    events = poller.poll(0)
    if any(flags & select.POLLNVAL for _, flags in events):
        raise RuntimeError("AT-SPI cleanup descriptor is invalid")
    return any(flags & select.POLLIN for _, flags in events)


def _wait_exit(descriptor: int, target: dict, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not _exited(descriptor, target):
        if time.monotonic() >= deadline:
            return False
        select.select([], [], [], min(0.01, max(0, deadline - time.monotonic())))
    return True


def _stop_target(descriptor: int, target: dict) -> None:
    if _exited(descriptor, target):
        return
    signal.pidfd_send_signal(descriptor, signal.SIGTERM)
    if not _wait_exit(descriptor, target, 3.0):
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        if not _wait_exit(descriptor, target, 3.0):
            raise RuntimeError(f"AT-SPI process {target['pid']} survived cleanup")


def _terminate_and_reap(proc: subprocess.Popen) -> None:
    """Only for an unreaped, directly acquired child; verify bounded final wait."""
    try:
        proc.terminate()
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)


def _cleanup_targets(data: dict, descriptors: dict, children: list) -> None:
    errors = []
    direct = {child.pid: child for child in children}
    ordered = list(reversed(data["targets"][:3])) + list(reversed(data["targets"][3:]))
    for target in ordered:
        try:
            if target["pid"] in direct:
                _terminate_and_reap(direct[target["pid"]])
            elif target["pid"] in descriptors:
                _stop_target(descriptors[target["pid"]], target)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            errors.append(str(error))
    for target in data["targets"]:
        if target["pid"] in direct:
            continue
        try:
            fields = Path(f"/proc/{target['pid']}/stat").read_text().rsplit(")", 1)[1].split()
            if fields[19] == target["birth"] and fields[0] == "Z" and int(fields[1]) == os.getpid():
                os.waitpid(target["pid"], os.WNOHANG)
        except (FileNotFoundError, ProcessLookupError, ChildProcessError):
            pass
    if errors:
        raise RuntimeError("AT-SPI cleanup incomplete: " + "; ".join(errors))



class AtSpiBusAdapter:
    """Starts and stops the AT-SPI2 accessibility bus.

    Creates an isolated D-Bus session bus, then launches
    at-spi-bus-launcher and at-spi2-registryd on it. Sets
    IsEnabled=true and the optional legacy ScreenReaderEnabled property.
    """

    def __init__(self) -> None:
        self._managed_procs: Dict[int, List[subprocess.Popen]] = {}

    def start_bus(self, display: str) -> BusStartResult:
        """Start isolated AT-SPI2 infrastructure for a display.

        Process architecture:
          1. dbus-daemon --session (isolated session bus)
          2. at-spi-bus-launcher (registers org.a11y.Bus)
          3. at-spi2-registryd (AT-SPI registry on the AT-SPI bus)
          4. Introspect org.a11y.Status on the isolated session bus
          5. Set supported activation properties true before app launch

        Returns BusStartResult with the dbus-daemon PID and the real
        session bus address.

        On any failure after dbus-daemon starts, all already-started
        processes are cleaned up before re-raising.
        """
        _require_pidfds()
        boot = _boot_id()
        base_env = {
            "HOME": os.environ.get("HOME", "/tmp"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        xdg = os.environ.get("XDG_RUNTIME_DIR", "")
        if xdg:
            base_env["XDG_RUNTIME_DIR"] = xdg

        descriptors: dict = {}
        targets: list = []
        # Step 1: Start isolated dbus-daemon
        dbus_proc = subprocess.Popen(
            ["dbus-daemon", "--session", "--print-address", "--nofork"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=base_env,
        )

        session_bus_address = dbus_proc.stdout.readline().decode().strip()

        # Validate dbus-daemon started correctly
        if dbus_proc.poll() is not None:
            raise RuntimeError(
                f"dbus-daemon exited immediately (exit code {dbus_proc.returncode})"
            )

        if not session_bus_address or not session_bus_address.startswith("unix:"):
            # Clean up the failed dbus-daemon
            _terminate_and_reap(dbus_proc)
            raise RuntimeError(
                "dbus-daemon did not return a valid bus address: "
                f"{session_bus_address!r}"
            )

        # dbus-daemon is running — track for cleanup on partial failure
        started: List[subprocess.Popen] = [dbus_proc]
        data = {
            "version": 1, "boot": boot, "uid": os.getuid(),
            "pid": dbus_proc.pid, "status": "active", "home": str(Path.home()),
            "bus": session_bus_address, "display": display, "targets": targets,
        }
        published = False
        try:
            descriptor, identity = _acquire_child(dbus_proc, base_env)
            descriptors[dbus_proc.pid] = descriptor
            identity["role"] = "session"
            targets.append(identity)
            # Step 2: Start at-spi-bus-launcher with the session bus
            launcher = _resolve_launcher()
            launcher_env = dict(base_env)
            launcher_env["GSETTINGS_BACKEND"] = "memory"
            launcher_env["DISPLAY"] = display
            launcher_env["DBUS_SESSION_BUS_ADDRESS"] = session_bus_address

            launcher_proc = subprocess.Popen(
                [launcher, "--launch-immediately"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=launcher_env,
            )
            started.append(launcher_proc)
            descriptor, identity = _acquire_child(launcher_proc, launcher_env)
            descriptors[launcher_proc.pid] = descriptor
            identity["role"] = "launcher"
            targets.append(identity)

            time.sleep(1.0)

            # Step 3: Get the AT-SPI bus address from the launcher
            # The launcher creates a SEPARATE D-Bus instance for AT-SPI.
            # We need this address for the registryd.
            atspi_bus_result = subprocess.run(
                [
                    "dbus-send",
                    f"--bus={session_bus_address}",
                    "--dest=org.a11y.Bus",
                    "--print-reply",
                    "/org/a11y/bus",
                    "org.a11y.Bus.GetAddress",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            atspi_bus_addr = ""
            for line in atspi_bus_result.stdout.splitlines():
                line = line.strip()
                if line.startswith("string"):
                    # Extract address from: string "unix:path=..."
                    atspi_bus_addr = line.split('"')[1] if '"' in line else ""
                    break

            # Step 4: Start at-spi2-registryd with the SESSION bus address.
            # registryd discovers the AT-SPI bus via org.a11y.Bus.GetAddress()
            # on the session bus — it needs the session bus, not the AT-SPI bus.
            registryd = _resolve_registryd()
            registryd_env = dict(base_env)
            registryd_env["DBUS_SESSION_BUS_ADDRESS"] = session_bus_address

            registryd_proc = subprocess.Popen(
                [registryd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=registryd_env,
            )
            started.append(registryd_proc)
            descriptor, identity = _acquire_child(registryd_proc, registryd_env)
            descriptors[registryd_proc.pid] = descriptor
            identity["role"] = "registry"
            targets.append(identity)

            time.sleep(0.5)

            data["atspi_bus"] = atspi_bus_addr
            _inventory(data, descriptors)
            with _locked_receipt(dbus_proc.pid, create=True) as path:
                if path.exists() or path.is_symlink():
                    raise RuntimeError("AT-SPI bus PID already reserved in this boot")
                _publish_receipt(path, data, exclusive=True)
                published = True

            # Discover the actual service API before writing activation properties.
            has_screen_reader = _has_screen_reader_property(session_bus_address)
            activation_properties = ["IsEnabled"]
            if has_screen_reader:
                activation_properties.insert(0, "ScreenReaderEnabled")
            for name in activation_properties:
                subprocess.run(
                    [
                        "dbus-send",
                        f"--bus={session_bus_address}",
                        "--dest=org.a11y.Bus",
                        "--print-reply",
                        "/org/a11y/bus",
                        "org.freedesktop.DBus.Properties.Set",
                        "string:org.a11y.Status",
                        f"string:{name}",
                        "variant:boolean:true",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=5,
                )
            _inventory(data, descriptors)
            with _locked_receipt(dbus_proc.pid, create=True) as path:
                _read_receipt(path, dbus_proc.pid)
                _publish_receipt(path, data)
        except Exception as error:
            # Inventory before parents exit, then reap all direct acquisitions.
            cleanup_error = None
            try:
                _inventory(data, descriptors)
            except (OSError, RuntimeError) as failure:
                cleanup_error = failure
            if published:
                try:
                    with _locked_receipt(dbus_proc.pid) as path:
                        _read_receipt(path, dbus_proc.pid)
                        _publish_receipt(path, data)
                except (OSError, RuntimeError, ValueError) as failure:
                    cleanup_error = failure
            try:
                _cleanup_targets(data, descriptors, started)
            except (OSError, RuntimeError) as failure:
                cleanup_error = failure
            finally:
                recorded = {target["pid"] for target in targets}
                for proc in reversed(started):
                    if proc.pid in recorded:
                        continue
                    try:
                        _terminate_and_reap(proc)
                    except (OSError, subprocess.TimeoutExpired) as failure:
                        cleanup_error = failure
            if cleanup_error is not None:
                if not published:
                    # Preserve known identities privately if cleanup cannot be proven.
                    try:
                        with _locked_receipt(dbus_proc.pid, create=True) as path:
                            failure_path = path.with_name(f"failed-{dbus_proc.pid}-{time.monotonic_ns()}.json")
                            _publish_receipt(failure_path, data, exclusive=True)
                    except (OSError, RuntimeError) as retention_error:
                        raise RuntimeError(f"AT-SPI startup failed: {error}; cleanup failed: {cleanup_error}; ownership retention failed: {retention_error}") from error
                raise RuntimeError(f"AT-SPI startup failed: {error}; cleanup failed: {cleanup_error}") from error
            if published:
                with _locked_receipt(dbus_proc.pid) as path:
                    _read_receipt(path, dbus_proc.pid)
                    retired = {key: value for key, value in data.items() if key != "targets"}
                    retired["status"] = "retired"
                    _publish_receipt(path, retired)
            raise
        finally:
            for descriptor in descriptors.values():
                os.close(descriptor)

        # Track all Popen objects for cleanup
        self._managed_procs[dbus_proc.pid] = started

        return BusStartResult(
            pid=dbus_proc.pid,
            bus_address=session_bus_address,
            atspi_bus_address=atspi_bus_addr or None,
        )

    def stop_bus(self, pid: int) -> None:
        """Recover private ownership, pin/validate every target, then verify exit."""
        descriptors: dict = {}
        try:
            _require_pidfds()
            with _locked_receipt(pid) as path:
                data = _read_receipt(path, pid)
                if data["status"] == "retired":
                    return
                children = self._managed_procs.get(pid, [])
                # Unreaped direct child handles cannot be retargeted to a recycled PID.
                _recover_targets(data, descriptors, {child.pid for child in children})
                _inventory(data, descriptors)
                # Persist descendants before any destructive action for retry safety.
                _publish_receipt(path, data)
                _cleanup_targets(data, descriptors, children)
                data.pop("targets")
                data["status"] = "retired"
                _publish_receipt(path, data)
                self._managed_procs.pop(pid, None)
        except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"AT-SPI ownership cleanup refused: {error}") from error
        finally:
            for descriptor in descriptors.values():
                os.close(descriptor)

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from autopush.device_control import (
    DeviceBusyError,
    DeviceControlLease,
    device_mutex_name,
)


def test_device_mutex_name_is_stable_and_device_specific():
    first = device_mutex_name("emulator-5554")
    second = device_mutex_name(" EMULATOR-5554 ")
    other = device_mutex_name("127.0.0.1:16384")

    assert first == second
    assert first == "MAA-LocalAI-Device-04AB3FC382BFB33E"
    assert first != other


@pytest.mark.skipif(os.name != "nt", reason="named mutex is Windows-only")
def test_device_lease_is_exclusive_across_processes():
    address = f"test-{uuid.uuid4().hex}"
    repository = Path(__file__).resolve().parents[3]
    code = (
        "import sys; "
        "from autopush.device_control import DeviceControlLease; "
        f"lease=DeviceControlLease({address!r}); "
        "lease.acquire(); "
        "print(lease.name, flush=True); "
        "sys.stdin.readline(); "
        "lease.release()"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository / "tools" / "auto-push")
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        assert process.stdout is not None
        holder_name = process.stdout.readline().strip()
        assert holder_name == device_mutex_name(address)

        contender = DeviceControlLease(address)
        with pytest.raises(DeviceBusyError):
            contender.acquire()

        assert process.stdin is not None
        process.stdin.write("\n")
        process.stdin.flush()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    recovered = DeviceControlLease(address, timeout_seconds=2)
    recovered.acquire()
    recovered.release()

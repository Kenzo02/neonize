import asyncio
from unittest.mock import patch

import pytest

from neonize.aioze.client import NewAClient
from neonize.client import NewClient
from neonize.proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps
from neonize.utils.device import get_default_device_props


@pytest.mark.parametrize(
    ("system_name", "expected_os"),
    [
        ("Darwin", "macOS"),
        ("Windows", "Windows"),
        ("Linux", "Linux"),
        ("FreeBSD", "FreeBSD"),
        ("  FreeBSD  ", "FreeBSD"),
        ("", "Desktop"),
        ("   ", "Desktop"),
    ],
)
def test_get_default_device_props_uses_truthful_host_identity(system_name, expected_os):
    with patch("platform.system", return_value=system_name):
        props = get_default_device_props()

    assert props.os == expected_os
    assert props.platformType == DeviceProps.DESKTOP
    assert props.os != "Neonize"

    serialized = props.SerializeToString()
    restored = DeviceProps.FromString(serialized)
    assert restored.os == expected_os
    assert restored.platformType == DeviceProps.DESKTOP


def test_sync_client_preserves_caller_supplied_device_props():
    class ClientRecorder:
        def __init__(self):
            self.args = None

        def Neonize(self, *args):
            self.args = args
            return None

    props = DeviceProps(os="Custom Desktop", platformType=DeviceProps.SAFARI)
    expected = props.SerializeToString()
    client = NewClient("sync-device-props", props=props)
    recorder = ClientRecorder()
    client._NewClient__client = recorder

    with patch(
        "neonize.client.get_default_device_props",
        side_effect=AssertionError("default props must not replace caller props"),
    ):
        client.connect_with_proxy()

    assert recorder.args is not None
    assert recorder.args[11] == expected
    assert recorder.args[12] == len(expected)


def test_async_client_preserves_caller_supplied_device_props():
    class ClientRecorder:
        def __init__(self):
            self.args = None

        async def Neonize(self, *args):
            self.args = args
            return None

    async def exercise():
        props = DeviceProps(os="Custom Desktop", platformType=DeviceProps.SAFARI)
        expected = props.SerializeToString()
        client = NewAClient("async-device-props", props=props)
        recorder = ClientRecorder()
        client._NewAClient__client = recorder

        with patch(
            "neonize.aioze.client.get_default_device_props",
            side_effect=AssertionError("default props must not replace caller props"),
        ):
            connect_task = await client.connect_with_proxy()
            await connect_task

        return recorder.args, expected

    args, expected = asyncio.run(exercise())
    assert args is not None
    assert args[11] == expected
    assert args[12] == len(expected)

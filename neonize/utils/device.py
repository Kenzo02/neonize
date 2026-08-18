import platform

from ..proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps


def get_default_device_props() -> DeviceProps:
    """Build default linked-device properties from the current host OS."""
    system_name = platform.system().strip()
    if system_name == "Darwin":
        system_name = "macOS"
    elif not system_name:
        system_name = "Desktop"

    return DeviceProps(os=system_name, platformType=DeviceProps.DESKTOP)

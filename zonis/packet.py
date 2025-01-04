from typing import TypedDict, Any, Optional, Literal, Type, Dict, Union, Tuple

from zonis.exceptions import (
    BaseZonisException,
    DuplicateConnection,
    UnhandledWebsocketType,
)

# Closure codes can be between 3000-4999
custom_close_codes: Dict[int, Type[BaseZonisException]] = {
    4102: DuplicateConnection,
    3001: UnhandledWebsocketType,
}

# Add new type for IPv6 address information
AddressInfo = Union[
    Tuple[str, int],  # IPv4: (address, port)
    Tuple[str, int, int, int]  # IPv6: (address, port, flowinfo, scopeid)
]

class ConnectionInfo(TypedDict):
    address: AddressInfo
    family: Literal["IPv4", "IPv6"]

class Packet(TypedDict):
    data: Any
    type: Literal[
        "IDENTIFY",
        "REQUEST",
        "SUCCESS_RESPONSE",
        "FAILURE_RESPONSE",
        "CLIENT_REQUEST",
        "CLIENT_REQUEST_RESPONSE",
    ]
    identifier: str


class RequestPacket(TypedDict):
    route: str
    arguments: Dict[str, Any]


class IdentifyDataPacket(TypedDict):
    override_key: Optional[str]
    secret_key: str


class IdentifyPacket(TypedDict):
    identifier: str
    type: Literal["IDENTIFY"]
    data: dict[str, Union[str, ConnectionInfo]]


class ClientToServerPacket(TypedDict):
    identifier: str
    type: Literal["CLIENT_REQUEST"]
    data: RequestPacket

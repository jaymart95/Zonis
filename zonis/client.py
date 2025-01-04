import asyncio
import logging
import websockets
import socket

import signal
from typing import Optional, Dict, Any, Union, Tuple


from zonis import (
    Packet,
    Router,
    RouteHandler,
    UnknownPacket,
    UnhandledWebsocketType,
)
from zonis.packet import (
    RequestPacket,
    IdentifyDataPacket,
    ClientToServerPacket,
)

log = logging.getLogger(__name__)


class Client(RouteHandler):
    """
    Parameters
    ----------
    reconnect_attempt_count: :class:`int`
        The number of times that the :class:`Client` should
        attempt to reconnect.
    url: :class:`str`
        Defaults to ``ws://localhost``.
    port: Optional[:class:`int`]
        The port that the :class:`Client` should use.
    """

    def __init__(
        self,
        host: str = "::1",
        port: int = 8000,
        identifier: str = "DEFAULT",
        *,
        secret_key: str = "",
        override_key: Optional[str] = None,
        ipv6: bool = True,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._identifier = identifier
        self._secret_key = secret_key
        self._override_key = override_key
        self.ipv6 = ipv6
        
        self._router: Optional[Router] = None
        self.__is_open = False

        # https://github.com/gearbot/GearBot/blob/live/GearBot/GearBot.py
        try:
            for signame in ("SIGINT", "SIGTERM", "SIGKILL"):
                asyncio.get_event_loop().add_signal_handler(
                    getattr(signal, signame),
                    lambda: asyncio.ensure_future(self.close()),
                )
        except Exception as e:
            pass  # doesn't work on windows

    async def block_until_closed(self):
        """A blocking call which releases when the WS closes."""
        await self.router.block_until_closed()

    async def start(self) -> None:
        """Start the IPC client."""
        self.load_routes()
        await self._create_connection()
        log.info(
            "Successfully connected to the server with identifier %s",
            self.identifier,
        )

    async def close(self) -> None:
        """Stop the IPC client."""
        await self.router.close()
        log.info("Successfully closed the client")

    async def _create_connection(self) -> None:
        """Create a connection to the server with IPv6 support"""
        try:
            # Try to resolve the hostname first
            try:
                addrinfo = socket.getaddrinfo(
                    self.host,
                    self.port,
                    socket.AF_UNSPEC,
                    socket.SOCK_STREAM,
                )
                # Check if the resolved address is IPv6
                is_ipv6 = any(family == socket.AF_INET6 for family, *_ in addrinfo)
                if is_ipv6 and not self.host.startswith('['):
                    ws_url = f"ws://[{self.host}]:{self.port}"
                else:
                    ws_url = f"ws://{self.host}:{self.port}"
            except socket.gaierror:
                # If resolution fails, try direct connection with original host
                if ':' in self.host and not self.host.startswith('['):
                    ws_url = f"ws://[{self.host}]:{self.port}"
                else:
                    ws_url = f"ws://{self.host}:{self.port}"
            
            # Create connection
            websocket = await websockets.connect(
                ws_url,
                family=socket.AF_INET6 if self.ipv6 else socket.AF_INET
            )
            
            self._router = Router(self._identifier, websocket)
            self._router.register_receiver(self._request_handler)
            
            await self._router.connect_client(
                secret_key=self._secret_key,
                override_key=self._override_key,
            )
            
            self.__is_open = True
            
        except Exception as e:
            log.error(f"Connection failed: {str(e)}")
            raise ConnectionError(f"Failed to connect: {e}")

    def _is_ip_address(self, host: str) -> bool:
        """Check if the host is an IP address"""
        try:
            socket.inet_pton(socket.AF_INET6 if self.ipv6 else socket.AF_INET, host)
            return True
        except (socket.error, ValueError):
            return False

    async def _request_handler(self, packet_data, resolution_handler):
        data: RequestPacket = packet_data["data"]
        route_name = data["route"]
        if route_name not in self._routes:
            await resolution_handler(
                data=Packet(
                    identifier=self.identifier,
                    type="FAILURE_RESPONSE",
                    data=f"{route_name} is not a valid route name.",
                )
            )
            return

        if route_name in self._instance_mapping:
            result = await self._routes[route_name](
                self._instance_mapping[route_name],
                **data["arguments"],
            )
        else:
            result = await self._routes[route_name](**data["arguments"])

        await resolution_handler(
            data=Packet(
                identifier=self.identifier,
                type="SUCCESS_RESPONSE",
                data=result,
            )
        )

    async def request(self, route: str, **kwargs):
        """Make a request to the server"""
        request_future: asyncio.Future = await self.router.send(
            ClientToServerPacket(
                identifier=self.identifier,
                type="CLIENT_REQUEST",
                data=RequestPacket(route=route, arguments=kwargs),
            )
        )
        data: Packet = await request_future
        if "type" not in data:
            log.debug("Failed to resolve packet type for %s", data)
            raise UnknownPacket

        if "data" not in data:
            log.debug(
                "Failed to resolve packet as it was missing the 'data' field: %s",
                data,
            )
            raise UnknownPacket

        if data["type"] != "SUCCESS_RESPONSE":
            raise UnhandledWebsocketType(
                f"Client.request expected a packet of type "
                f"SUCCESS_RESPONSE. Received {data['type']}"
            )

        return data["data"]

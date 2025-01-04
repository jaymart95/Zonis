import socket
import selectors
import logging
import secrets
import traceback
from typing import Dict, Literal, Any, cast, Optional, Callable, Tuple, Union

from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

from zonis import (
    Packet,
    UnknownClient,
    RequestFailed,
    BaseZonisException,
    DuplicateConnection,
    Router,
    RouteHandler,
    FastAPIWebsockets,
)
from zonis.packet import RequestPacket, IdentifyPacket
from zonis.router import PacketT

log = logging.getLogger(__name__)


class Server(RouteHandler):
    """
    Parameters
    ----------
    using_fastapi_websockets: :class:`bool`
        Defaults to ``False``.
    override_key: Optional[:class:`str`]
    secret_key: :class:`str`
        Defaults to an emptry string.
    """

    def __init__(
        self,
        host: str = "::",
        port: int = 8000,
        backlog: int = 100,
        dual_stack: bool = True
    ):
        self.host = host
        self.port = port
        self.backlog = backlog
        self.dual_stack = dual_stack
        self.selector = selectors.DefaultSelector()
        self.sock: Optional[socket.socket] = None
        self.running = False
        self._setup_logging()

    def _setup_socket(self) -> None:
        """Setup the server socket with IPv6 support"""
        # Use AF_INET6 for IPv6 support
        self.sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        
        # Enable dual-stack socket (IPv4 + IPv6) if requested
        if self.dual_stack:
            try:
                self.sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (AttributeError, socket.error) as e:
                self.logger.warning(f"Could not enable dual-stack mode: {e}")

        # Set socket options
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setblocking(False)
        
        try:
            self.sock.bind((self.host, self.port))
            self.sock.listen(self.backlog)
        except socket.error as e:
            self.logger.error(f"Failed to bind to {self.host}:{self.port}: {e}")
            raise

    def accept(self) -> Tuple[socket.socket, Union[Tuple[str, int], Tuple[str, int, int, int]]]:
        """Accept a connection and return client socket and address"""
        client, addr = self.sock.accept()
        client.setblocking(False)
        return client, addr

    def _setup_logging(self) -> None:
        self.logger = logging.getLogger(__name__)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    async def disconnect(self, identifier: str) -> None:
        """Disconnect a client connection.

        Parameters
        ----------
        identifier: str
            The client identifier to disconnect

        Notes
        -----
        This doesn't yet tell the client to stop
        gracefully, this just removes it from our store.

        """
        router = self._connections.pop(identifier)
        await router.close()

    async def request(
        self, route: str, *, client_identifier: str = "DEFAULT", **kwargs
    ) -> Any:
        """Make a request to the provided IPC client.

        Parameters
        ----------
        route: str
            The IPC route to call.
        client_identifier: Optional[str]
            The client to make a request to.

            This only applies in many to one setups
            or if you changed the default identifier.
        kwargs
            All the arguments you wish to invoke the IPC route with.

        Returns
        -------
        Any
            The data the IPC route returned.

        Raises
        ------
        RequestFailed
            The IPC request failed.
        """
        conn = self._connections.get(client_identifier)
        if not conn:
            raise UnknownClient

        request_future = await conn.send(
            Packet(
                identifier=client_identifier,
                type="REQUEST",
                data=RequestPacket(route=route, arguments=kwargs),
            )
        )
        packet = await request_future
        if packet["type"] == "FAILURE_RESPONSE":
            raise RequestFailed(packet["data"])

        return packet["data"]

    async def request_all(self, route: str, **kwargs) -> Dict[str, Any]:
        """Issue a request to connected IPC clients.

        Parameters
        ----------
        route: str
            The IPC route to call.
        kwargs
            All the arguments you wish to invoke the IPC route with.

        Returns
        -------
        Dict[str, Any]
            A dictionary where the keys are the client
            identifiers and the values are the returned data.

            The data could also be an instance of :py:class:RequestFailed:
        """
        results: Dict[str, Any] = {}

        for i, conn in self._connections.items():
            try:
                request_future = await conn.send(
                    Packet(
                        identifier=i,
                        type="REQUEST",
                        data=RequestPacket(route=route, arguments=kwargs),
                    )
                )
                packet: Packet = await request_future
                if packet["type"] == "FAILURE_RESPONSE":
                    results[i] = RequestFailed(packet["data"])
                else:
                    results[i] = packet["data"]
            except ConnectionClosedOK as e:
                results[i] = RequestFailed("Connection Closed")
                log.error(
                    "request_all connection closed: %s, %s",
                    i,
                    "".join(traceback.format_exception(e)),
                )
            except ConnectionClosedError as e:
                results[i] = RequestFailed(
                    f"Connection closed with error: {e.code}|{e.reason}"
                )
                log.error(
                    "request_all connection closed with error: %s, %s",
                    i,
                    "".join(traceback.format_exception(e)),
                )
            except Exception as e:
                results[i] = RequestFailed("Request failed.")
                log.error(
                    "request_all connection threw: %s, %s",
                    i,
                    "".join(traceback.format_exception(e)),
                )

        return results

    async def parse_identify(self, packet: PacketT, websocket) -> str:
        """Parse a packet to establish a new valid client connection.

        Parameters
        ----------
        packet: Packet
            The packet to read
        websocket
            The websocket this connection is using

        Returns
        -------
        str
            The established clients identifier

        Raises
        ------
        BaseZonisException
            Unexpected WS issue
        DuplicateConnection
            Duplicate connection without override keys
        """
        raw_packet = packet
        packet = raw_packet["data"]
        identifier: str = packet.get("identifier")
        try:
            ws_type: Literal["IDENTIFY"] = packet["type"]
            if ws_type != "IDENTIFY":
                await websocket.close(
                    code=4101, reason=f"Expected IDENTIFY, received {ws_type}"
                )
                raise BaseZonisException(
                    f"Unexpected ws response type, expected IDENTIFY, received {ws_type}"
                )

            packet: IdentifyPacket = cast(IdentifyPacket, packet)
            secret_key = packet["data"]["secret_key"]
            if secret_key != self._secret_key:
                await websocket.close(code=4100, reason=f"Invalid secret key.")
                raise BaseZonisException(
                    f"Client attempted to connect with an incorrect secret key."
                )

            override_key = packet["data"].get("override_key")
            if identifier in self._connections and (
                not override_key or override_key != self._override_key
            ):
                await websocket.close(
                    code=4102, reason="Duplicate identifier on IDENTIFY"
                )
                raise DuplicateConnection("Identify failed.")

            router: Router = Router(identifier, FastAPIWebsockets(websocket))
            router.register_receiver(callback=self._request_handler)

            await router.connect_server()
            self._connections[identifier] = router
            await router.send_response(
                packet_id=raw_packet["packet_id"],
                data=Packet(identifier=identifier, type="IDENTIFY", data=None),
            )
            return identifier
        except Exception as e:
            self._connections.pop(identifier, None)
            raise BaseZonisException("Identify failed") from e

    async def _request_handler(self, packet_data, resolution_handler):
        data: RequestPacket = packet_data["data"]
        route_name = data["route"]
        if route_name not in self._routes:
            await resolution_handler(
                data=Packet(
                    identifier="SERVER",
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
                identifier="SERVER",
                type="SUCCESS_RESPONSE",
                data=result,
            )
        )

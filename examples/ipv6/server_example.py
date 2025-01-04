import asyncio
from zonis import Server

server = Server(
    host="::",  # Listen on all IPv6 interfaces
    port=8000,
    dual_stack=True  # Enable dual-stack mode to accept both IPv4 and IPv6
)

@server.route()
async def get_server_info():
    return {
        "status": "running",
        "protocol": "IPv6",
        "dual_stack": True
    }

async def main():
    # Make a request to all connected clients
    responses = await server.request_all("ping")
    print(f"Ping responses: {responses}")
    
    # Make a request to a specific client
    response = await server.request("echo", client_identifier="EXAMPLE_CLIENT", message="Hello IPv6!")
    print(f"Echo response: {response}")

if __name__ == "__main__":
    asyncio.run(main()) 
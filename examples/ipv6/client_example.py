import asyncio
from zonis import Client

client = Client(
    host="::1",  # IPv6 localhost
    port=8000,
    identifier="EXAMPLE_CLIENT",
    ipv6=True  # Explicitly enable IPv6
)

@client.route()
async def ping():
    return "pong"

@client.route()
async def echo(message: str):
    return message

async def main():
    await client.start()
    await client.block_until_closed()

if __name__ == "__main__":
    asyncio.run(main()) 
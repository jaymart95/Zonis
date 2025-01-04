from fastapi import FastAPI, WebSocket
from zonis import Server

app = FastAPI()
server = Server(
    host="::",  # Listen on all IPv6 interfaces
    port=8000,
    dual_stack=True  # Enable dual-stack mode
)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await server.handle_websocket(websocket)

@server.route()
async def get_status():
    return {
        "status": "online",
        "protocol": "IPv6",
        "server_type": "FastAPI"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="::",  # Listen on all IPv6 interfaces
        port=8000
    ) 
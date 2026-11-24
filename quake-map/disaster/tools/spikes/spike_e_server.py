"""Spike E/D server (drone Phase 0): serves the tablet test page and measures the link. Throw-away; touches no app code.

    python tools/spikes/spike_e_server.py --three /path/to/Drone-SLAM/droneforest/viewer/vendor/three.module.js [--port 8001]

Then, on the tablet (pick the first that works; see docs/integration/DISCOVERY.md section E):
    A) USB:      adb reverse tcp:8001 tcp:8001      -> Chrome on the tablet: http://localhost:8001/
    B) hotspot:  python tools/https_proxy.py --target 127.0.0.1:8001 --port 8444   -> https://<laptop ip>:8444/
Press "Run all" on the page, then "Send report"; the report is printed here and saved next to this script.
Also serves /vendor/three.module.js with CORS so spike D (spike_d_overlay.js) can be pasted into the real dashboard.
"""
import argparse, json, pathlib, time
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

HERE = pathlib.Path(__file__).resolve().parent


def make_app(three: pathlib.Path):
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/")
    def page():
        return FileResponse(HERE / "spike_tablet" / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/vendor/three.module.js")
    def vendor():
        return Response(three.read_bytes(), media_type="text/javascript", headers={"Cache-Control": "max-age=3600"})

    @app.get("/spike_d_overlay.js")
    def overlay():
        return FileResponse(HERE / "spike_d_overlay.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/echo")
    async def echo(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                msg = await ws.receive_text()
                await ws.send_text(msg)            # echoed untouched: the page measures the round trip itself
        except WebSocketDisconnect:
            pass

    @app.post("/api/report")
    async def report(req: Request):
        body = await req.json()
        body["server_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        body["client_host"] = req.client.host if req.client else None
        out = HERE / f"spike_e_report_{int(time.time())}.json"
        out.write_text(json.dumps(body, indent=1))
        print("\n=== report from", body["client_host"], "saved to", out.name, "===")
        print(json.dumps(body.get("summary", body), indent=1))
        return JSONResponse({"ok": True, "saved": out.name})

    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--three", required=True, help="path to three.module.js (Drone-SLAM ships r170 under droneforest/viewer/vendor)")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    uvicorn.run(make_app(pathlib.Path(a.three)), host=a.host, port=a.port, log_level="warning")

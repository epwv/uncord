#qr code login the same remote auth flow the discord app uses
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from typing import Callable, Optional

import aiohttp

GATEWAY = "wss://remote-auth-gateway.discord.gg/?v=2"
LOGIN_URL = "https://discord.com/api/v9/users/@me/remote-auth/login"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


class LoginError(Exception):
    pass


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


async def qrLogin(showQr: Callable[[str], None], status: Callable[[str], None]) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    oaep = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)

    def decrypt(b64: str) -> bytes:
        return key.decrypt(base64.b64decode(b64), oaep)

    headers = {"Origin": "https://discord.com", "User-Agent": UA}
    async with aiohttp.ClientSession(headers=headers) as http:
        async with http.ws_connect(GATEWAY, heartbeat=None, max_msg_size=0) as ws:
            beat: Optional[asyncio.Task] = None
            try:
                while True:
                    msg = await ws.receive()
                    if msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSING,
                    ):
                        raise LoginError(f"Discord closed the login session ({ws.close_code}). Try again.")
                    if msg.type is aiohttp.WSMsgType.ERROR:
                        raise LoginError(f"connection error: {ws.exception()}")
                    if msg.type is not aiohttp.WSMsgType.TEXT:
                        continue
                    data = json.loads(msg.data)
                    op = data.get("op")
                    if op == "hello":
                        interval = data.get("heartbeat_interval", 41250) / 1000

                        async def heartbeat() -> None:
                            while True:
                                await asyncio.sleep(interval)
                                await ws.send_json({"op": "heartbeat"})

                        beat = asyncio.create_task(heartbeat())
                        await ws.send_json(
                            {"op": "init", "encoded_public_key": base64.b64encode(pub).decode()}
                        )
                    elif op == "nonce_proof":
                        nonce = decrypt(data["encrypted_nonce"])
                        await ws.send_json(
                            {"op": "nonce_proof", "proof": _b64url(hashlib.sha256(nonce).digest())}
                        )
                    elif op == "pending_remote_init":
                        showQr(f"https://discord.com/ra/{data['fingerprint']}")
                        status("Scan this with the Discord app: Settings → Scan QR Code. Waiting…")
                    elif op == "pending_ticket":
                        user = decrypt(data["encrypted_user_payload"]).decode(errors="replace").split(":")
                        name = user[3] if len(user) > 3 else "your account"
                        status(f"Scanned as {name}. Approve the login on your phone…")
                    elif op == "pending_login":
                        status("Approved. Finishing login…")
                        async with http.post(
                            LOGIN_URL,
                            json={"ticket": data["ticket"]},
                            headers={"Content-Type": "application/json"},
                        ) as r:
                            body = await r.json(content_type=None)
                        if r.status != 200 or "encrypted_token" not in (body or {}):
                            if body and ("captcha_key" in body or "captcha_sitekey" in body):
                                raise LoginError(
                                    "Discord wants a captcha for this login, which can't be solved in a "
                                    "terminal. Use the token option instead."
                                )
                            raise LoginError(f"login failed ({r.status}): {str(body)[:160]}")
                        return decrypt(body["encrypted_token"]).decode()
                    elif op == "cancel":
                        raise LoginError("Login was cancelled on the phone.")
            finally:
                if beat is not None:
                    beat.cancel()


def qrText(url: str) -> str:
    import segno

    qr = segno.make(url, error="m")
    rows = [list(r) for r in qr.matrix]
    size = len(rows)
    q = 2
    grid = (
        [[0] * (size + 2 * q) for _ in range(q)]
        + [[0] * q + r + [0] * q for r in rows]
        + [[0] * (size + 2 * q) for _ in range(q)]
    )
    if len(grid) % 2:
        grid.append([0] * (size + 2 * q))
    out = []
    for y in range(0, len(grid), 2):
        line = []
        for top, bot in zip(grid[y], grid[y + 1]):
            line.append({(0, 0): "█", (1, 1): " ", (1, 0): "▄", (0, 1): "▀"}[(top, bot)])
        out.append("\x1b[97;40m" + "".join(line) + "\x1b[0m")
    return "\n".join(out)

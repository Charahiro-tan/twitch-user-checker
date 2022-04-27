import asyncio
import random
import string
import urllib.parse
from datetime import datetime

import config
import motor.motor_asyncio
import ujson as json
import uvloop
from aiohttp import ClientSession, web

from web import html


class Handler:
    def __init__(self) -> None:
        self.session: ClientSession = None
        # fmt: off
        self.db_client: motor.motor_asyncio.core.AgnosticClient = motor.motor_asyncio.AsyncIOMotorClient(config.db_address, config.db_port)
        self.db_client.get_io_loop = asyncio.get_event_loop
        self.db: motor.motor_asyncio.core.AgnosticDatabase = self.db_client["ban_app"]
        self.db_state: motor.motor_asyncio.core.AgnosticCollection = self.db["state"]
        self.db_user: motor.motor_asyncio.core.AgnosticCollection = self.db["user"]
        # fmt: on

    async def handle_root(self, request: web.Request):
        mode = request.query.get("mode")

        if not mode:
            raise web.HTTPFound(location=config.github_url)

        match mode:
            case "block":
                raise web.HTTPFound(location=await self._get_auth_url("block"))
            case "ban":
                raise web.HTTPFound(location=await self._get_auth_url("ban"))
            case "timeout":
                raise web.HTTPFound(location=await self._get_auth_url("timeout"))
            case "block_ban":
                raise web.HTTPFound(location=await self._get_auth_url("block_ban"))
            case "block_timeout":
                raise web.HTTPFound(location=await self._get_auth_url("block_timeout"))
            case _:
                raise web.HTTPFound(location=config.github_url)

    async def handle_authorization(self, request: web.Request):
        code = request.query.get("code")
        state = request.query.get("state")
        error = request.query.get("error")

        if not code and not error:
            raise web.HTTPFound(location=config.github_url)

        if error:
            return self._error_responce(error="未承認")

        if not self.session:
            self.session = ClientSession()

        result = await self._validate_state(state)
        if not result:
            return self._error_responce(error="無効なセッション又は時間切れ")

        token: dict = await self._fetch_token(code)
        if not token:
            return self._error_responce(error="トークン取得失敗")

        user: dict = await self._fetch_user(token["access_token"])
        if not user:
            return self._error_responce(error="ユーザー取得失敗")

        db_result = await self._db_write(token, user)
        if not db_result:
            return self._error_responce(error="データベース書き込み失敗")

        return self._success_responce(name=user["display_name"])

    async def _fetch_token(self, code: str) -> dict:
        URL = "https://id.twitch.tv/oauth2/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        params = {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": config.redirect_uri,
        }
        async with self.session.post(URL, params=params, headers=headers) as res:
            if res.status == 200:
                data: dict = json.loads(await res.read())
            else:
                data = {}
        return data

    async def _fetch_user(self, token: str) -> dict:
        URL = "https://api.twitch.tv/helix/users"
        headers = {"Authorization": f"Bearer {token}", "Client-Id": config.client_id}
        async with self.session.get(URL, headers=headers) as res:
            if res.status == 200:
                data = json.loads(await res.read())
                try:
                    data = data["data"][0]
                except KeyError:
                    data = {}
            else:
                data = {}
        return data

    async def _validate_token(self, token: str) -> dict:
        URL = "https://id.twitch.tv/oauth2/validate"
        headers = {"Authorization": f"OAuth {token}"}
        async with self.session.get(URL, headers=headers) as res:
            if res.status == 200:
                data = json.loads(await res.read())
            else:
                data = {}
        return data

    async def _get_auth_url(self, mode: str):
        BASE = "https://id.twitch.tv/oauth2/authorize?"

        match mode:
            case "block":
                scope = " ".join(config.block_scope)
            case "ban":
                scope = " ".join(config.ban_scope)
            case "timeout":
                scope = " ".join(config.ban_scope | config.timeout_scope)
            case "block_ban":
                scope = " ".join(config.block_scope | config.ban_scope)
            case "block_timeout":
                scope = " ".join(
                    config.block_scope | config.ban_scope | config.timeout_scope
                )

        state = "".join(random.choices(string.ascii_letters + string.digits, k=30))
        await self.db_state.insert_one({"createdAt": datetime.utcnow(), "state": state})
        params = {
            "response_type": "code",
            "force_verify": "true",
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": scope,
            "state": state,
        }
        return BASE + urllib.parse.urlencode(params)

    async def _validate_state(self, state: str) -> bool:
        result = await self.db_state.find_one({"state": state})
        if result:
            await self.db_state.delete_one({"state": state})
            return True
        else:
            return False

    async def _db_write(self, token: dict, user: dict) -> bool:
        data = {}
        try:
            data["access_token"] = token["access_token"]
            data["refresh_token"] = token["refresh_token"]
            data["client_id"] = config.client_id
            data["login"] = user["login"]
            data["user_id"] = user["id"]
            data["scope"] = token["scope"]
        except KeyError:
            return False
        result = await self.db_user.find_one({"user_id": data["user_id"]})
        if result:
            await self.db_user.replace_one({"user_id": data["user_id"]}, data)
        else:
            await self.db_user.insert_one(data)
        return True

    def _success_responce(self, name: str) -> web.Response:
        return web.Response(
            text=html.success.replace(r"{{name}}", name), content_type="text/html"
        )

    def _error_responce(self, error: str = "不明なエラー") -> web.Response:
        return web.Response(
            text=html.error.replace(r"{{error}}", error), content_type="text/html"
        )


def start():
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    app = web.Application()
    handler = Handler()
    app.add_routes([web.get("/", handler.handle_root),web.get("/auth", handler.handle_authorization),])  # fmt: skip
    web.run_app(app, port=config.server_port)

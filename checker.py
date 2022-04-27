import asyncio
import datetime
import os
import re
import sys

import aiofiles
import aiohttp
import logzero
import motor.motor_asyncio  # motor
import ujson as json
from dateutil import parser  # python-dateutil

import config

logger: logzero.logging = logzero.logger


class Checker:
    def __init__(self):
        self.stop: bool = False
        self.stoped: bool = False
        self.stop_json: str = "stop.json"
        self.session: aiohttp.ClientSession = None
        self.client_id: str = config.checker_client_id
        self.token: str = None
        # fmt: off
        self.db_client: motor.motor_asyncio.core.AgnosticClient = motor.motor_asyncio.AsyncIOMotorClient(config.db_address, config.db_port)
        self.db_client.get_io_loop = asyncio.get_event_loop
        self.db: motor.motor_asyncio.core.AgnosticDatabase = self.db_client["ban_app"]
        self.db_checker: motor.motor_asyncio.core.AgnosticCollection = self.db["checker"]
        self.db_user: motor.motor_asyncio.core.AgnosticCollection = self.db["user"]
        # fmt: on
        self.last_id: int = None
        self.ban_user: list[re.Pattern] = [re.compile(s) for s in config.ban_user]
        self.ban_queue = asyncio.Queue()
        self.retry_queue = asyncio.Queue()


    async def start(self):
        self.session = aiohttp.ClientSession(json_serialize=json.dumps)

        self.token = await self.db_checker.find_one({"name": "token"})
        if self.token:
            self.token = self.token["token"]

        self.last_id = await self.db_checker.find_one({"name": "last_id"})
        if self.last_id:
            self.last_id = self.last_id["id"]
        else:
            self.last_id = config.init_id

        while not self.token:
            await self._fetch_client_credentials()

        asyncio.create_task(self._fetch_new_user())
        asyncio.create_task(self._ban_task())
        asyncio.create_task(self._retry_ban_task())
        asyncio.create_task(self.stopper())


    async def stopper(self):
        async with aiofiles.open(self.stop_json, 'r') as f:
            json_ = json.loads(await f.read())
        if json_['stop']:
            json_['stop'] = False
            async with aiofiles.open(self.stop_json, 'w') as f:
                await f.write(json.dumps(json_, indent=4))
        while True:
            async with aiofiles.open(self.stop_json, "r") as f:
                json_ = json.loads(await f.read())
            if json_["stop"]:
                logger.info("Stop Task Start")
                self.stop = True
                json_['stop'] = False
                async with aiofiles.open(self.stop_json, 'w') as f:
                    await f.write(json.dumps(json_, indent=4))
                break
            else:
                await asyncio.sleep(5)
        while not self.stoped:
            await asyncio.sleep(0)
        logger.info('STOP')
        loop = asyncio.get_running_loop()
        loop.stop()


    async def _fetch_client_credentials(self) -> bool:
        URL = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": self.client_id,
            "client_secret": config.checker_client_secret,
            "grant_type": "client_credentials",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        for i in range(1, 11):
            async with self.session.post(URL, params=params, headers=headers) as res:
                if res.status == 200:
                    logger.info("Fetch Client Credentials")
                    data: dict = json.loads(await res.read())
                else:
                    await asyncio.sleep(i)
                    continue
            self.token = data["access_token"]
            _db = await self.db_checker.find_one({"name": "token"})
            if _db:
                await self.db_checker.replace_one({"name": "token"}, {"name": "token", "token": self.token})  # fmt: skip
            else:
                await self.db_checker.insert_one({"name": "token", "token": self.token})
            break
        else:
            logger.error(f"Feth Client Credentials Error Status: {res.status}")
            return False

        return True


    async def _fetch_refresh_token(self, user: dict) -> str:
        URL = "https://id.twitch.tv/oauth2/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        params = {
            "grant_type": "refresh_token",
            "client_id": user["client_id"],
            "client_secret": config.client_secret,
            "refresh_token": user["refresh_token"],
        }

        for i in range(1, 11):
            async with self.session.post(URL, headers=headers, params=params) as res:
                if res.status == 200:
                    logger.info(f"Token Refresh Success user: {user['login']}")
                    data: dict = json.loads(await res.read())
                    break
                elif res.status == 401 or res.status == 400:
                    logger.info(f"User Delete name: {user['login']} id: {user['user_id']}")  # fmt: skip
                    await self.db_user.delete_one({"user_id": user["user_id"]})
                    return ''
                else:
                    await asyncio.sleep(i)
                    continue
        else:
            logger.error(f"Token Refresh Error")
            logger.error(f"user_name: {user['login']}")
            logger.error(f"status: {res.status}")
            return ''

        user_info = await self._fetch_user_info(data["access_token"], user["client_id"])

        new_token = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "client_id": user["client_id"],
            "login": user_info['data'][0]["login"],
            "user_id": user_info['data'][0]["id"],
            "scope": data["scope"],
        }

        await self.db_user.replace_one({"user_id": user_info['data'][0]["id"]}, new_token)
        return data["access_token"]


    async def _fetch_user_info(self, token: str, client_id: str) -> dict:
        URL = "https://api.twitch.tv/helix/users"
        headers = {"Authorization": f"Bearer {token}", "Client-Id": client_id}
        for i in range(1, 11):
            async with self.session.get(URL, headers=headers) as res:
                if res.status == 200:
                    return json.loads(await res.read())
                else:
                    await asyncio.sleep(i)
                    continue
        else:
            return {}


    async def _fetch_new_user(self) -> None:
        URL = "https://api.twitch.tv/helix/users?"
        while not self.stop:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Client-Id": self.client_id,
            }
            params = [f"id={self.last_id + i}" for i in range(1, config.count + 1)]
            params = "&".join(params)
            async with self.session.get(URL + params, headers=headers) as res:

                ratelimit = int(res.headers.get("ratelimit-remaining", 800))

                if res.status == 200:
                    data: list = json.loads(await res.read())["data"]
                elif res.status == 401:
                    logger.error(f"Fetch User Error Status: {res.status}")
                    await self._fetch_client_credentials()
                    continue
                else:
                    logger.error(f"Fetch User Error Status: {res.status}")
                    await asyncio.sleep(60)
                    continue

            if data:
                await self._id_check(data)

            interval = 1 if ratelimit > 100 else 10
            await asyncio.sleep(interval)
        self.ban_queue.put_nowait("stop")
        return


    async def _id_check(self, data: list) -> None:
        id_list: list[int] = []
        for user in data:
            id_list.append(int(user["id"]))
            for _re in self.ban_user:
                result = _re.search(user["login"])
                if result:
                    asyncio.create_task(self.discord_hook(user))
                    self.ban_queue.put_nowait(user)
                    break
        self.last_id = max(id_list)
        db_result = await self.db_checker.find_one({"name": "last_id"})
        if db_result:
            await self.db_checker.replace_one({"name": "last_id"}, {"name": "last_id", "id": self.last_id})  # fmt: skip
        else:
            await self.db_checker.insert_one({"name": "last_id", "id": self.last_id})
        return


    async def _ban_task(self):
        while True:
            ban_user = await self.ban_queue.get()
            if ban_user == "stop":
                break

            logger.info(f"Hit: {ban_user}")
            async for user in self.db_user.find():
                tasks = []
                if "user:manage:blocked_users" in user["scope"]:
                    tasks.append(self._request_block(ban_user, user))
                if "moderator:manage:banned_users" in user["scope"]:
                    tasks.append(self._request_ban(ban_user, user))
                ratelimit = await asyncio.gather(*tasks)
                interval = 1 if min(ratelimit) > 100 else 5
                await asyncio.sleep(interval)
        self.retry_queue.put_nowait("stop")
        return


    async def _retry_ban_task(self):
        while True:
            retry_task = await self.retry_queue.get()
            if retry_task == "stop":
                break
            
            if retry_task['retry_count'] > 3:
                continue
            
            retry_task['retry_count'] += 1
            
            db_data = await self.db_user.find_one({'user_id': retry_task['user']['user_id']})
            token = retry_task['user']['access_token']
            
            if not db_data:
                continue
            
            if retry_task['code'] == 401:
                if db_data['access_token'] == token:
                    token = await self._fetch_refresh_token(retry_task['user'])
                    if not token:
                        continue
                else:
                    token = db_data['access_token']
            
            retry_task['user']['access_token'] = token
            
            if retry_task['method'] == 'ban':
                ratelimit = await self._request_ban(retry_task['ban_user'], retry_task['user'], retry_task['retry_count'])
            elif retry_task['method'] == 'block':
                ratelimit = await self._request_block(retry_task['ban_user'], retry_task['user'], retry_task['retry_count'])
            
            interval = 1 if ratelimit > 100 else 10
            await asyncio.sleep(interval)
        self.stoped = True
        return


    async def _request_ban(self, ban_user: dict, user: dict, retry: int = 0) -> int:
        URL = "https://api.twitch.tv/helix/moderation/bans"
        headers = {
            "Authorization": f"Bearer {user['access_token']}",
            "Client-Id": user["client_id"],
            "Content-Type": "application/json",
        }
        params = {"broadcaster_id": user["user_id"], "moderator_id": user["user_id"]}
        body = {"data": {"user_id": ban_user["id"], "reason": "(Automatically Banned)"}}
        if "bits:read" in user["scope"]:
            body["data"]["duration"] = 64800

        async with self.session.post(URL, params=params, headers=headers, json=body) as res: # fmt: skip

            ratelimit = int(res.headers.get("ratelimit-remaining", 800))

            if res.status == 200:
                return ratelimit

            elif res.status == 400 or res.status == 403:
                logger.error(f"Ban Error code:{res.status}")
                logger.error(f"user: {user}")
                logger.error(f"ban_user: {ban_user}")
                return ratelimit

            else:
                logger.error(f"Ban Error code:{res.status}")
                logger.error(f"user: {user}")
                logger.error(f"ban_user: {ban_user}")
                data = {
                    "method": "ban",
                    "retry_count": retry,
                    "code": res.status,
                    "ratelimit": ratelimit,
                    "user": user,
                    "ban_user": ban_user,
                }
                self.retry_queue.put_nowait(data)
                return ratelimit


    async def _request_block(self, ban_user: dict, user: dict, retry: int = 0) -> int:
        URL = "https://api.twitch.tv/helix/users/blocks"
        headers = {
            "Authorization": f"Bearer {user['access_token']}",
            "Client-Id": user["client_id"],
        }
        params = {"target_user_id": ban_user["id"], "reason": "other"}
        async with self.session.put(URL, headers=headers, params=params) as res:

            ratelimit = int(res.headers.get("ratelimit-remaining", 800))

            if res.status == 204:
                return ratelimit

            else:
                data = {
                    "method": "block",
                    "retry_count": retry,
                    "code": res.status,
                    "ratelimit": ratelimit,
                    "user": user,
                    "ban_user": ban_user,
                }
                self.retry_queue.put_nowait(data)
                return ratelimit
    
    
    async def discord_hook(self, ban_user: dict) -> None:
        created_at = parser.isoparse(ban_user['created_at'])
        created_at_jst = created_at + datetime.timedelta(hours=9)
        body = {
            "embeds": [
                {
                    "title": ban_user['login'],
                    'url': f"https://www.twitch.tv/{ban_user['login']}",
                    "timestamp": (datetime.datetime.utcnow()).strftime('%Y-%m-%dT%H:%M:%SZ'),
                    "color": 0xff0000,
                    "thumbnail": {
                        "url": ban_user['profile_image_url']
                    },
                    "fields": [
                        {
                            "name": "✒️login id",
                            "value": ban_user["login"],
                            "inline": True
                        },
                        {
                            "name": "📺display name",
                            "value": ban_user["display_name"],
                            "inline": True
                        },
                        {
                            "name": "🆔id",
                            "value": ban_user["id"],
                            "inline": True
                        },
                        {
                            "name": "🕰️created at (JST)",
                            "value": created_at_jst.strftime('%Y/%m/%d %H:%M:%S'),
                            "inline": True
                        }
                    ]
                }
            ]
        }
        res = await self.session.post(config.discord_url, json=body)
        res.close()

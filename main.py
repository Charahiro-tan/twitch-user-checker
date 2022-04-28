import asyncio
import multiprocessing as mp
from datetime import datetime

import logzero
import uvloop

import config
from checker import Checker
from web import web

now = datetime.now().strftime("%Y_%m_%d")
logzero.loglevel(config.loglevel)
logzero.logfile(f"log/logfile{now}.log", loglevel=10)
logger: logzero.logging = logzero.logger

if __name__ == "__main__":
    logger.info("START")

    mp.set_start_method("fork")
    server = mp.Process(target=web.start, daemon=True)
    server.start()

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    checker = Checker()
    loop.run_until_complete(checker.start())
    loop.run_forever()
    server.terminate()

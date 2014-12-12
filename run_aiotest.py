import aiotest.run
import asyncio
import sys
if sys.platform == 'win32':
    from asyncio.windows_utils import socketpair
else:
    from socket import socketpair

config = aiotest.TestConfig()
config.asyncio = asyncio
config.socketpair = socketpair
config.new_event_pool_policy = asyncio.DefaultEventLoopPolicy
config.call_soon_check_closed = True
aiotest.run.main(config)

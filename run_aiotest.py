import aiotest.run
import sys
import trollius
if sys.platform == 'win32':
    from trollius.windows_utils import socketpair
else:
    from socket import socketpair

config = aiotest.TestConfig()
config.asyncio = trollius
config.socketpair = socketpair
config.new_event_pool_policy = trollius.DefaultEventLoopPolicy
config.call_soon_check_closed = True
aiotest.run.main(config)

import orm, asyncio
from models import User, Blog, Comment


async def test(loop):
    await orm.create_pool(loop = loop, user = 'root', password = '123456', db = 'awesome')
    user = User(name = 'Duke', email = '1246008291@qq.com', passwd = '18844199352',image = 'about:blank')
    #await user.save()
    await orm.destory_pool()#必须先销毁连接池

loop = asyncio.get_event_loop()
loop.run_until_complete(test(loop))
loop.close()

import asyncio
from asyncio import StreamReader, StreamWriter


async def dispatch(reader: StreamReader, writer: StreamWriter):
    addr = writer.get_extra_info('peername')
    while True:
        data = await reader.read(2048)

        if data and data != b"exit\n":
            writer.write(data)
            print('{} sent: {}'.format(addr, data))
        else:
            await writer.drain()
            writer.close()
            return


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    coro = asyncio.start_server(dispatch, '127.0.0.1', 5555, loop=loop)
    server = loop.run_until_complete(coro)
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    server.close()
    loop.run_until_complete(server.wait_closed())
    loop.close()

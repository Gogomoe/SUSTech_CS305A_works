import asyncio
import os
from mimetypes import MimeTypes
from urllib.parse import unquote
from typing import Tuple
from typing import Dict
from asyncio import StreamReader, StreamWriter

err404 = [b'HTTP/1.0 404 Not Found\r\n'
          b'Connection: close\r\n'
          b'Content-Type:text/html; charset=utf-8\r\n'
          b'\r\n'
          b'<html><body>404 Not Found<body></html>\r\n'
          b'\r\n'][0]

err405 = [b'HTTP/1.0 405 Method Not Allowed\r\n'
          b'Connection: close\r\n'
          b'Content-Type:text/html; charset=utf-8\r\n'
          b'\r\n'
          b'<html><body>405 Method Not Allowed<body></html>\r\n'
          b'\r\n'][0]

# the root dir map to web page
mappingDir = "."


async def dispatch(reader: StreamReader, writer: StreamWriter):
    # we assume that a request not bigger than 128k
    # otherwise, my program may throw exception and cannot sever correctly
    data = await reader.read(128 * 1024)
    request = decode_request(data)
    method, uri, query, header = request

    if method != 'GET' and method != 'HEAD':
        await write(err405, writer)
        return

    rel_uri = uri[1:] if uri[0] == '/' else uri
    path = os.path.join(mappingDir, unquote(rel_uri))
    if not os.path.exists(path):
        await write(err404, writer)
    elif os.path.isdir(path):
        await handleDir(request, writer)
    else:
        await handleFile(request, writer)


def decode_request(data: bytes) -> Tuple[str, str, str, Dict[str, str]]:
    data = data.decode().split('\r\n')
    method: str
    uri: str
    method, uri, _ = data[0].split()

    query: str = ''
    if '?' in uri:
        index = uri.index('?')
        query = uri[index + 1:]
        uri = uri[:index]

    header = {}

    # print(data[0])
    for line in data[1:]:
        # print(line)
        if not line:
            # when the line is empty, header is end
            break
        index = line.index(":")
        name: str = line[0: index]
        value: str = line[index + 1:]
        header[name.strip().lower()] = value.strip()

    return method, uri, query, header


async def write(data: bytes, writer: StreamWriter):
    writer.write(data)
    await writer.drain()
    writer.close()


async def handleDir(request: Tuple[str, str, str, Dict[str, str]], writer: StreamWriter):
    method, uri, query, header = request

    rel_uri = uri[1:] if uri[0] == '/' else uri
    unquote_uri = unquote(rel_uri)
    path = os.path.join(mappingDir, unquote_uri)

    data = b'HTTP/1.0 200 OK\r\n'
    data += b'Connection: close\r\n'
    data += b'Content-Type: text/html; charset=utf-8\r\n'
    data += b'\r\n'

    if method == "HEAD":
        await write(data, writer)
        return

    data += b'<html>\r\n'
    data += '<head><title>Index of /{}</title></head>\r\n'.format(unquote_uri).encode('utf-8')
    data += b'<body bgcolor="white">\r\n'
    data += '<h1>Index of /{}</h1><hr><pre>\r\n'.format(unquote_uri).encode('utf-8')

    if uri == '/':
        data += '<a href="/">..</a>\r\n'.encode('utf-8')
    else:
        data += '<a href="../">..</a>\r\n'.encode('utf-8')

    for it in os.listdir(path):
        abs_path = os.path.join(path, it)
        if os.path.isdir(abs_path):
            it += '/'
        data += '<a href="{}">{}</a>\r\n'.format(it, it).encode('utf-8')

    data += b'</pre><hr></body>\r\n'
    data += b'</html>\r\n'

    await write(data, writer)


async def handleFile(request: Tuple[str, str, str, Dict[str, str]], writer: StreamWriter):
    method, uri, query, header = request

    rel_uri = uri[1:] if uri[0] == '/' else uri
    unquote_uri = unquote(rel_uri)
    path = os.path.join(mappingDir, unquote_uri)

    mine = MimeTypes()
    guess = mine.guess_type(path)
    if not guess:
        mine_type = "application/octet-stream"
    else:
        mine_type, _ = guess

    content_length = os.path.getsize(path)

    data = b'HTTP/1.0 200 OK\r\n'
    data += b'Connection: close\r\n'
    data += 'Content-Type: {}\r\n'.format(mine_type).encode('utf-8')
    data += 'Content-Length: {}\r\n'.format(content_length).encode('utf-8')
    data += b'\r\n'

    if method == "HEAD":
        await write(data, writer)
        return

    file = open(path, 'rb')
    data += file.read()
    file.close()

    await write(data, writer)


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    coro = asyncio.start_server(dispatch, '127.0.0.1', 8080, loop=loop)
    server = loop.run_until_complete(coro)
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    server.close()
    loop.run_until_complete(server.wait_closed())
    loop.close()

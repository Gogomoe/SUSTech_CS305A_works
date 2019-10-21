import socket
import os
import threading
from mimetypes import MimeTypes
from urllib.parse import unquote
from typing import Tuple, Any, List
from typing import Dict

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
mappingDir = "../"

Request = Tuple[str, str, str, Dict[str, str]]
Respond = Tuple[Tuple[int, str], Dict[str, str], bytes]


class Server(threading.Thread):
    def __init__(self, conn: socket.socket, address: Tuple[str, int]):
        threading.Thread.__init__(self)
        self.conn = conn
        self.address = address

    def run(self):
        data = self.conn.recv(10 * 1024 * 1024)
        request = decode_request(data)
        method, uri, query, header = request

        if method != 'GET' and method != 'HEAD':
            write(err405, self.conn)
            return

        rel_uri = uri[1:] if uri[0] == '/' else uri
        path = os.path.join(mappingDir, unquote(rel_uri))
        if not os.path.exists(path):
            write(err404, self.conn)
        elif os.path.isdir(path):
            handleDir(request, self.conn)
        else:
            handleFile(request, self.conn)


def decode_request(data: bytes) -> Request:
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


def write(data: bytes, conn: socket.socket):
    conn.send(data)
    conn.close()


def handleDir(request: Request, conn: socket):
    method, uri, query, header = request

    rel_uri = uri[1:] if uri[0] == '/' else uri
    unquote_uri = unquote(rel_uri)
    path = os.path.join(mappingDir, unquote_uri)

    respond: Respond = ((200, "OK"), {}, b'')

    respond[1]['Connection'] = 'close'
    respond[1]['Content-Type'] = 'text/html; charset=utf-8'

    if method == "HEAD":
        write(make_data(respond), conn)
        return

    if uri == '/' and 'cookie' in header and 'referer' not in header:
        cookie = header['cookie']
        cookies = list(map(lambda it: it.strip(), cookie.split(';')))
        cookies = list(map(lambda it: (it.split('=')[0], it.split('=')[1]), cookies))
        visit_list: List[Tuple[str, str]] = list(filter(lambda it: it[0] == 'visit', cookies))
        if len(visit_list) != 0:
            visit: str = visit_list[0][1]
            if visit != '/':
                respond_status = (302, 'Found')
                respond[1]['Location'] = visit
                write(make_data((respond_status, respond[1], respond[2])), conn)
                return

    respond[1]['Set-Cookie'] = 'visit={}; path=/'.format(uri)

    body = b''
    body += b'<html>\r\n'
    body += '<head><title>Index of /{}</title></head>\r\n'.format(unquote_uri).encode('utf-8')
    body += b'<body bgcolor="white">\r\n'
    body += '<h1>Index of /{}</h1><hr><pre>\r\n'.format(unquote_uri).encode('utf-8')

    if uri == '/':
        body += '<a href="/">..</a>\r\n'.encode('utf-8')
    else:
        body += '<a href="../">..</a>\r\n'.encode('utf-8')

    for it in os.listdir(path):
        abs_path = os.path.join(path, it)
        if os.path.isdir(abs_path):
            it += '/'
        body += '<a href="{}">{}</a>\r\n'.format(it, it).encode('utf-8')

    body += b'</pre><hr></body>\r\n'
    body += b'</html>\r\n'

    respond = (respond[0], respond[1], body)

    write(make_data(respond), conn)


def handleFile(request: Request, conn: socket):
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

    file_size = os.path.getsize(path)

    respond: Respond = ((200, "OK"), {}, b'')

    respond[1]['Accept-Ranges'] = 'bytes'
    respond[1]['Server'] = 'GoHttp/0.6'
    respond[1]['Connection'] = 'close'
    respond[1]['Content-Type'] = mine_type
    respond[1]['Content-Length'] = str(file_size)

    if method == "HEAD":
        write(make_data(respond), conn)
        return

    file = open(path, 'rb')
    body = file.read()
    file.close()

    respond = (respond[0], respond[1], body)

    respond = handleRange(request, respond)

    write(make_data(respond), conn)


def handleRange(request: Request, respond: Respond) -> Respond:
    class RangeException(Exception):
        pass

    method, uri, query, header = request
    respond_status, respond_header, body = respond
    file_size = len(body)

    if 'range' not in header:
        return respond

    try:
        range = header['range']
        start_pos = range.index('=')
        end_pos = range.index('-')

        range_from = int(range[start_pos + 1:end_pos])
        range_to: int

        if '-' == range[-1]:
            range_to = file_size - 1
        else:
            range_to = int(range[end_pos + 1:])
            range_to = min(file_size - 1, range_to)

        if range_from < 0 or range_from > range_to:
            raise RangeException()

        respond_status = (206, 'Partial Content')
        respond_header['Content-Range'] = 'bytes {}-{}/{}'.format(str(range_from), str(range_to),
                                                                  str(file_size))
        respond_header['Content-Length'] = str(range_to - range_from + 1)
        body = body[range_from:range_to + 1]

    except RangeException:
        respond_status = (416, 'Requested Range Not Satisfiable')
        respond_header['Content-Length'] = '0'
        respond_header['Content-Range'] = 'bytes */{}'.format(str(file_size))
        body = b''

    return respond_status, respond_header, body


def make_data(respond: Respond) -> bytes:
    (status_code, status_str), header, body = respond
    data = 'HTTP/1.1 {} {}\r\n'.format(str(status_code), status_str).encode('utf-8')
    for k, v in header.items():
        data += '{}: {}\r\n'.format(k, v).encode('utf-8')
    data += b'\r\n'
    data += body
    return data


def web():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 8080))
    sock.listen(10)
    while True:
        conn, address = sock.accept()
        Server(conn, address).start()


if __name__ == "__main__":
    try:
        web()
    except KeyboardInterrupt:
        exit()

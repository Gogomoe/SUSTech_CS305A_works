import socket
import os
import threading
from mimetypes import MimeTypes
from urllib.parse import unquote
from typing import Tuple, Any, List
from typing import Dict

# the root dir map to web page
mappingDir = "."

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

        rel_uri = uri[1:] if uri[0] == '/' else uri
        path = os.path.join(mappingDir, unquote(rel_uri))

        respond: Respond = ((200, "OK"), {}, b'')
        respond[1]['Connection'] = 'close'
        respond[1]['Server'] = 'GoHttp/0.6'

        if method != 'GET' and method != 'HEAD':
            respond = handle405(request, respond)
            write(make_data(respond), self.conn)
            return
        if not os.path.exists(path):
            respond = handle404(request, respond)
            write(make_data(respond), self.conn)
            return

        respond = handle302(request, respond)
        if request[0][0] == 302:
            write(make_data(respond), self.conn)
            return

        if os.path.isdir(path):
            respond = handleDir(request, respond)
        else:
            respond = handleFile(request, respond)

        respond = handleRange(request, respond)

        if method == 'HEAD':
            write(make_data((respond[0], respond[1], b'')), self.conn)
            return

        write(make_data(respond), self.conn)


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


def handle405(request, respond) -> Respond:
    status = (405, 'Method Not Allowed')
    body = b'<html><body>405 Method Not Allowed<body></html>'
    return status, respond[1], body


def handle404(request, respond):
    status = (404, 'Not Found')
    body = b'<html><body>404 Not Found<body></html>'
    return status, respond[1], body


def handle302(request, respond):
    method, uri, query, header = request

    if not (uri == '/' and 'cookie' in header and 'referer' not in header):
        return respond

    cookie = header['cookie']
    cookies = list(map(lambda it: it.strip(), cookie.split(';')))
    cookies = list(map(lambda it: (it.split('=')[0], it.split('=')[1]), cookies))
    visit_list: List[Tuple[str, str]] = list(filter(lambda it: it[0] == 'visit', cookies))

    if len(visit_list) == 0:
        return respond

    visit: str = visit_list[0][1]
    if visit == '/':
        return respond

    respond_status = (302, 'Found')
    respond[1]['Location'] = visit
    return respond_status, respond[1], b''


def handleDir(request: Request, respond: Respond) -> Respond:
    method, uri, query, header = request

    rel_uri = uri[1:] if uri[0] == '/' else uri
    unquote_uri = unquote(rel_uri)
    path = os.path.join(mappingDir, unquote_uri)

    respond[1]['Content-Type'] = 'text/html; charset=utf-8'
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

    return respond[0], respond[1], body


def handleFile(request: Request, respond: Respond) -> Respond:
    method, uri, query, header = request

    rel_uri = uri[1:] if uri[0] == '/' else uri
    unquote_uri = unquote(rel_uri)
    path = os.path.join(mappingDir, unquote_uri)

    guess = MimeTypes().guess_type(path)
    mine_type = guess[0] if guess else "application/octet-stream"

    file_size = os.path.getsize(path)

    respond[1]['Accept-Ranges'] = 'bytes'
    respond[1]['Content-Type'] = mine_type
    respond[1]['Content-Length'] = str(file_size)

    file = open(path, 'rb')
    body = file.read()
    file.close()

    return respond[0], respond[1], body


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


def write(data: bytes, conn: socket.socket):
    conn.send(data)
    conn.close()


def web():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('0.0.0.0', 8080))
    sock.listen(10)
    while True:
        conn, address = sock.accept()
        Server(conn, address).start()


if __name__ == "__main__":
    try:
        web()
    except KeyboardInterrupt:
        exit()

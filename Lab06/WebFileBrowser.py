import socket
import os
import threading
from mimetypes import MimeTypes
from urllib.parse import unquote
from typing import Tuple, Any
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


class Server(threading.Thread):
    def __init__(self, conn: socket.socket, address: Tuple[str, int]):
        threading.Thread.__init__(self)
        self.conn = conn
        self.address = address

    def run(self):
        data = self.conn.recv(128 * 1024)
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


def write(data: bytes, conn: socket.socket):
    conn.send(data)
    conn.close()


def handleDir(request: Tuple[str, str, str, Dict[str, str]], conn: socket):
    method, uri, query, header = request

    rel_uri = uri[1:] if uri[0] == '/' else uri
    unquote_uri = unquote(rel_uri)
    path = os.path.join(mappingDir, unquote_uri)

    data = b'HTTP/1.0 200 OK\r\n'
    data += b'Connection: close\r\n'
    data += b'Content-Type: text/html; charset=utf-8\r\n'
    data += b'\r\n'

    if method == "HEAD":
        write(data, conn)
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

    write(data, conn)


def handleFile(request: Tuple[str, str, str, Dict[str, str]], conn: socket):
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

    respond_status = (200, "OK")
    respond_header = {}
    respond_header['Connection'] = 'close'
    respond_header['Content-Type'] = mine_type
    respond_header['Content-Length'] = file_size

    if method == "HEAD":
        write(make_data(respond_status, respond_header, b''), conn)
        return

    file = open(path, 'rb')
    body = file.read()
    file.close()

    # TODO refactor and add auto range for big file
    if 'range' in header:
        range = header['range']
        start_pos = range.index('=')
        end_pos = range.index('-')

        if '-' == range[-1]:
            range_from = int(range[start_pos + 1:end_pos])
            if range_from < 0:
                respond_status = (416, 'Requested Range Not Satisfiable')
                respond_header['Content-Length'] = '0'
                respond_header['Content-Range'] = 'bytes */{}'.format(str(file_size))
                body = b''
            else:
                respond_status = (206, 'Partial Content')
                respond_header['Content-Range'] = 'bytes {}-{}/{}'.format(str(range_from), str(file_size - 1),
                                                                          str(file_size))
                respond_header['Content-Length'] = str(file_size - range_from)
                body = body[range_from:]
        else:
            range_from = int(range[start_pos + 1:end_pos])
            range_to = int(range[end_pos + 1:])
            if range_from < 0 or range_from > range_to or range_to >= file_size:
                respond_status = (416, 'Requested Range Not Satisfiable')
                respond_header['Content-Length'] = '0'
                respond_header['Content-Range'] = 'bytes */{}'.format(str(file_size))
                body = b''
            else:
                respond_status = (206, 'Partial Content')
                respond_header['Content-Range'] = 'bytes {}-{}/{}'.format(str(range_from), str(range_to),
                                                                          str(file_size))
                respond_header['Content-Length'] = str(range_to - range_from + 1)
                body = body[range_from:range_to + 1]

    write(make_data(respond_status, respond_header, body), conn)


def make_data(status: Tuple[int, str], header: Dict[str, str], body: bytes) -> bytes:
    (status_code, status_str) = status
    data = 'HTTP/1.0 {} {}\r\n'.format(str(status_code), status_str).encode('utf-8')
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

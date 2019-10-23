from rdt import socket

message = b'abcdefg'

if __name__ == "__main__":
    client = socket()
    client.connect(('127.0.0.1', 8888))
    client.send(message)
    data = client.recv(10 * 1024 * 1024)
    print(data)
    assert data == message
    client.close()

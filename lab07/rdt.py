from datetime import datetime
from queue import Queue
from threading import Thread, currentThread
from enum import Enum, auto
from typing import Tuple, List, Dict
from packet import Packet

from udp import UDPsocket
import socket as sock
from time_limit import time_limited

Address = Tuple[str, int]


class State(Enum):
    CLOSED = auto()
    LISTEN = auto()
    SYN_SENT = auto()
    SYN_RCVD = auto()
    ESTABLISHED = auto()


class StateMachine(Thread):
    def __init__(self, conn):
        Thread.__init__(self)
        self.conn: Connection = conn

    def run(self):
        conn = self.conn
        socket = conn.socket

        no_packet = 0
        while True:
            retransmit = []
            now = datetime.now().timestamp()
            for packet, send_time in conn.sending:
                if conn.seq >= packet.seq + packet.LEN:
                    continue
                if now - send_time >= 1.0:
                    print(conn.state, "retransmit ", end='')
                    conn.send_packet(packet)
                    retransmit.append((packet, now))
                else:
                    retransmit.append((packet, send_time))

            conn.sending = retransmit

            if len(conn.receive.queue) == 0 and len(conn.sends.queue) != 0 and \
                    len(conn.sending) == 0 and no_packet >= 3 and conn.state == State.ESTABLISHED:
                data = conn.sends.get()
                print(conn.state, "send", end='')
                conn.send_packet(Packet.create(conn.seq, conn.ack, data))

            packet: Packet
            try:
                packet = conn.receive.get(timeout=0.5)
                no_packet = 0
            except:
                no_packet += 1
                continue

            print(conn.state, "recv", packet)

            if packet.seq < conn.ack:
                print(conn.state, "resend ", end='')
                conn.send_packet(Packet.create(conn.seq, conn.ack, ACK=True))
                continue
            if packet.ACK:
                conn.seq = max(conn.seq, packet.ack)
            if packet.LEN != 0:
                conn.ack = max(conn.ack, packet.seq + packet.LEN)

            if conn.state == State.CLOSED and packet.SYN:
                conn.state = State.SYN_RCVD
                print(conn.state, "send ", end='')
                conn.send_packet(Packet.create(conn.seq, conn.ack, b'\xAC', SYN=True, ACK=True))
            elif conn.state == State.SYN_SENT and packet.SYN:
                conn.state = State.ESTABLISHED
                print(conn.state, "send ", end='')
                conn.send_packet(Packet.create(conn.seq, conn.ack, ACK=True))
            elif conn.state == State.SYN_RCVD and packet.ACK:
                assert packet.ack == 1
                conn.state = State.ESTABLISHED
            elif packet.LEN != 0:
                conn.message.put(packet)
                print(conn.state, "send ", end='')
                conn.send_packet(Packet.create(conn.seq, conn.ack, ACK=True))


class Connection():
    def __init__(self, client: Address, socket):
        self.client = client
        self.socket = socket
        self.state = State.CLOSED
        self.seq = 0
        self.ack = 0
        self.receive: Queue[Packet] = Queue()
        self.sends: Queue[bytes] = Queue()
        self.message: Queue[Packet] = Queue()
        self.sending: List[Tuple[Packet, float]] = []

        self.machine = StateMachine(self)
        self.machine.start()

    def recv(self, bufsize: int, flags: int = ...) -> bytes:
        return self.message.get().payload

    def send(self, data: bytes, flags: int = ...) -> int:
        print("push", len(data), "bytes")
        self.sends.put(data)
        return len(data)

    def close(self) -> None:
        pass

    def send_packet(self, packet: Packet):
        print(packet)
        self.socket.sendto(packet.to_bytes(), self.client)
        self.sending.append((packet, datetime.now().timestamp()))

    def on_recv_packet(self, packet: Packet):
        self.receive.put(packet)


# import provided class
class socket(UDPsocket):
    def __init__(self):
        super(socket, self).__init__()
        self.state = State.CLOSED
        self.receiver = None

        self.unhandled_conns: Queue = Queue()
        self.connections: Dict[Address, Connection] = {}

        self.connection = None

    def connect(self, address: Tuple[str, int]):  # send syn; receive syn, ack; send ack    # your code here
        assert self.state == State.CLOSED

        conn = Connection(address, self)
        self.connection = conn

        def receive():
            while True:
                try:
                    data, addr = self.recvfrom(10 * 1024 * 1024)
                    packet = Packet.from_bytes(data)
                    conn.on_recv_packet(packet)
                except:
                    pass

        self.receiver = Thread(target=receive)
        self.receiver.start()

        conn.state = State.SYN_SENT
        conn.send_packet(Packet.create(conn.seq, conn.ack, b'\xAC', SYN=True))

    def accept(self):  # receive syn; send syn, ack; receive ack    # your code here
        assert self.state in (State.CLOSED, State.LISTEN)
        self.state = State.LISTEN

        def receive():
            while True:
                try:
                    data, addr = self.recvfrom(10 * 1024 * 1024)
                    if addr not in self.connections:
                        conn = Connection(addr, self)
                        self.connections[addr] = conn
                        self.unhandled_conns.put(conn)
                    packet = Packet.from_bytes(data)
                    self.connections[addr].on_recv_packet(packet)
                except:
                    pass

        if not self.receiver:
            self.receiver = Thread(target=receive)
            self.receiver.start()

        conn = self.unhandled_conns.get()

        return conn, conn.client

    def recv(self, bufsize: int, flags: int = ...) -> bytes:
        assert self.connection
        return self.connection.recv(bufsize, flags)

    def send(self, data: bytes, flags: int = ...) -> int:
        assert self.connection
        return self.connection.send(data, flags)

    def close(self) -> None:
        pass

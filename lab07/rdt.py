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
        while True:
            if len(conn.receive.queue) == 0 and len(conn.sends.queue) != 0 and conn.to_ack == conn.seq:
                data = conn.sends.get()
                to_send = Packet.create(conn.seq, conn.ack, data)
                print("send", to_send)
                conn.send_packet(to_send)

            packet: Packet
            try:
                packet = conn.receive.get(timeout=0.5)
            except:
                continue

            print("recv", packet)

            if packet.seq < conn.ack:
                create = Packet.create(conn.seq, conn.ack, ACK=True)
                print("resend ack", create)
                socket.sendto(create.to_bytes(), conn.client)
                continue
            if packet.ACK:
                conn.seq = max(conn.seq, packet.ack)
            if packet.LEN != 0:
                conn.ack = max(conn.ack, packet.seq + packet.LEN)

            if conn.state == State.CLOSED and packet.SYN:
                conn.state = State.SYN_RCVD
                conn.send_packet(Packet.create(conn.seq, conn.ack, b'\xAC', SYN=True, ACK=True))
                conn.state = State.ESTABLISHED
            elif conn.state in (State.SYN_SENT, State.ESTABLISHED) and packet.SYN:
                create = Packet.create(conn.seq, conn.ack, ACK=True)
                print("send ack", create)
                socket.sendto(create.to_bytes(), conn.client)
            elif packet.LEN != 0:
                conn.message.put(packet)
                create = Packet.create(conn.seq, conn.ack, ACK=True)
                print("send ack", create)
                socket.sendto(create.to_bytes(), conn.client)


class Connection():
    def __init__(self, client: Address, socket):
        self.client = client
        self.socket = socket
        self.state = State.CLOSED
        self.seq = 0
        self.ack = 0
        self.to_ack = 0
        self.receive: Queue[Packet] = Queue()
        self.sends: Queue[bytes] = Queue()
        self.acks: Queue[Packet] = Queue()
        self.message: Queue[Packet] = Queue()

        self.machine = StateMachine(self)
        self.machine.start()

    def recv(self, bufsize: int, flags: int = ...) -> bytes:
        return self.message.get().payload

    def send(self, data: bytes, flags: int = ...) -> int:
        assert self.state == State.ESTABLISHED
        print("push", len(data), "bytes")
        self.sends.put(data)
        return len(data)

    def close(self) -> None:
        pass

    def send_packet(self, packet: Packet):

        success = [False]

        @time_limited(1)
        def transmit():
            t = currentThread()
            self.to_ack = self.seq + packet.LEN
            self.socket.sendto(packet.to_bytes(), self.client)
            while t.alive:
                ack = self.acks.get()
                if ack.ack >= self.to_ack:
                    success[0] = True
                    break

        while not success[0]:
            try:
                transmit()
            except:
                pass

    def on_recv_packet(self, packet: Packet):
        self.receive.put(packet)
        if packet.ACK:
            self.acks.put(packet)


# import provided class
class socket(UDPsocket):
    def __init__(self):
        super(socket, self).__init__()
        self.state = State.CLOSED
        self.receiver = None

        self.unhandled_conns: Queue = Queue()
        self.connections: Dict[Address, Connection] = {}

        self.client = None
        self.socket = self
        self.seq = 0
        self.ack = 0
        self.to_ack = 0
        self.receive: Queue[Packet] = Queue()
        self.sends: Queue[bytes] = Queue()
        self.acks: Queue[Packet] = Queue()
        self.message: Queue[Packet] = Queue()
        self.machine = None

    def connect(self, address: Tuple[str, int]):  # send syn; receive syn, ack; send ack    # your code here
        assert self.state == State.CLOSED

        def receive():
            while True:
                try:
                    data, addr = self.recvfrom(10 * 1024 * 1024)
                    packet = Packet.from_bytes(data)
                    self.on_recv_packet(packet)
                except:
                    pass

        self.receiver = Thread(target=receive)
        self.receiver.start()

        self.machine = StateMachine(self)
        self.machine.start()

        self.client = address
        self.state = State.SYN_SENT
        self.send_packet(Packet.create(self.seq, self.ack, b'\xAC', SYN=True))
        print("send ok")
        self.state = State.ESTABLISHED

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
        return self.message.get().payload

    def send(self, data: bytes, flags: int = ...) -> int:
        assert self.state == State.ESTABLISHED
        print("push", len(data), "bytes")
        self.sends.put(data)
        return len(data)

    def close(self) -> None:
        pass

    def send_packet(self, packet: Packet):

        success = [False]

        @time_limited(1)
        def transmit():
            t = currentThread()
            self.to_ack = self.seq + packet.LEN
            self.socket.sendto(packet.to_bytes(), self.client)
            while t.alive:
                ack = self.acks.get()
                if ack.ack >= self.to_ack:
                    success[0] = True
                    break

        while not success[0]:
            try:
                transmit()
            except:
                pass

    def on_recv_packet(self, packet: Packet):
        self.receive.put(packet)
        if packet.ACK:
            self.acks.put(packet)
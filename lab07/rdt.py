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
    FIN_WAIT_1 = auto()
    FIN_WAIT_2 = auto()
    TIME_WAIT = auto()
    CLOSE_WAIT = auto()
    LAST_ACK = auto()


class StateMachine(Thread):
    def __init__(self, conn):
        Thread.__init__(self)
        self.conn: Connection = conn

    def run(self):
        conn = self.conn
        socket = conn.socket

        no_packet = 0
        while True:
            now = datetime.now().timestamp()

            sending = conn.sending
            conn.sending = []
            for packet, send_time in sending:
                if conn.seq >= packet.seq + packet.LEN:
                    continue
                if now - send_time >= 1.0:
                    print(conn.state, "retransmit ", end='')
                    conn.send_packet(packet)
                else:
                    conn.sending.append((packet, send_time))

            # close
            if conn.state == State.TIME_WAIT and no_packet >= 6:
                conn.state = State.CLOSED
                print(conn.state)
                # TODO close

            # send data
            if len(conn.receive.queue) == 0 and len(conn.sends.queue) != 0 and \
                    len(conn.sending) == 0 and no_packet >= 3 and conn.state in (State.ESTABLISHED, State.FIN_WAIT_1):
                data = conn.sends.get()
                if isinstance(data, Packet):
                    to_send = Packet.create(conn.seq, conn.ack, data.payload, SYN=data.SYN, ACK=data.ACK, FIN=data.FIN)
                else:
                    to_send = Packet.create(conn.seq, conn.ack, data)
                print(conn.state, "send ", end='')
                conn.send_packet(to_send)

            # receive date
            packet: Packet
            try:
                packet = conn.receive.get(timeout=0.5)
                no_packet = 0
            except:
                no_packet += 1
                continue

            print(conn.state, "recv", packet)

            if packet.LEN != 0 and packet.seq < conn.ack:
                print(conn.state, "resend ", end='')
                conn.send_packet(Packet.create(conn.seq, conn.ack, ACK=True))
                continue
            if packet.ACK:
                conn.seq = max(conn.seq, packet.ack)
            if packet.LEN != 0:
                conn.ack = max(conn.ack, packet.seq + packet.LEN)

            not_arrive = [it for (it, send_time) in conn.sending if conn.seq < it.seq + it.LEN]
            all_packet_arrive = len(conn.sends.queue) == 0 and len(not_arrive) == 0

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
            # close
            elif conn.state == State.ESTABLISHED and packet.FIN:
                conn.send_packet(Packet.create(conn.seq, conn.ack, ACK=True))
                conn.state = State.CLOSE_WAIT
                if all_packet_arrive:
                    conn.send_packet(Packet.create(conn.seq, conn.ack, b'\xAF', FIN=True, ACK=True))
                    conn.state = State.LAST_ACK
            elif conn.state == State.FIN_WAIT_1 and all_packet_arrive:
                conn.state = State.FIN_WAIT_2
                if packet.FIN and packet.ACK:
                    conn.send_packet(Packet.create(conn.seq, conn.ack, ACK=True))
                    conn.state = State.TIME_WAIT
            elif conn.state == State.CLOSE_WAIT and all_packet_arrive:
                conn.send_packet(Packet.create(conn.seq, conn.ack, b'\xAF', FIN=True, ACK=True))
                conn.state = State.LAST_ACK
            elif conn.state in (State.FIN_WAIT_1, State.FIN_WAIT_2) and packet.FIN and packet.ACK:
                conn.send_packet(Packet.create(conn.seq, conn.ack, ACK=True))
                conn.state = State.TIME_WAIT
            elif conn.state == State.LAST_ACK and packet.ACK:
                conn.state = State.CLOSED
                print(conn.state)
                # TODO close socket and thread

            elif packet.LEN != 0:
                conn.message.put(packet)
                print(conn.state, "send ", end='')
                conn.send_packet(Packet.create(conn.seq, conn.ack, ACK=True))


class Connection:
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
        assert self.state not in (State.CLOSED, State.LISTEN,
                                  State.FIN_WAIT_1, State.FIN_WAIT_2, State.CLOSE_WAIT,
                                  State.TIME_WAIT, State.LAST_ACK)
        print("push", len(data), "bytes")
        self.sends.put(data)
        return len(data)

    def close(self) -> None:
        assert self.state in (State.SYN_RCVD, State.ESTABLISHED)
        self.sends.put(Packet.create(data=b'\xAF', FIN=True))
        self.state = State.FIN_WAIT_1

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
        if self.connection:  # client
            self.connection.close()
        elif self.connections:  # server
            # TODO
            pass
        else:
            raise Exception("Illegal state")

from collections import defaultdict

from dns.resolver import query, Answer, NoAnswer
import asyncio
from typing import Tuple, List, Dict
import struct
import ipaddress
from datetime import datetime
import copy

from enum import Enum, unique


@unique
class QType(Enum):
    A = 1
    NS = 2
    CNAME = 5
    MX = 15
    TXT = 16
    AAAA = 28


@unique
class QClass(Enum):
    IN = 1
    ANY = 255


class DNSHeader:
    Struct = struct.Struct('!6H')

    def __init__(self):
        self.data = None

        self.ID = None
        self.QR = None
        self.OpCode = None
        self.AA = None
        self.TC = None
        self.RD = None
        self.RA = None
        self.Z = None
        self.RCode = None
        self.QDCount = None
        self.ANCount = None
        self.NSCount = None
        self.ARCount = None

    def parse_header(self, data):
        self.data = data
        self.ID, misc, self.QDCount, self.ANCount, self.NSCount, self.ARCount = DNSHeader.Struct.unpack_from(data)

        self.QR = (misc & 0x8000) != 0
        self.OpCode = (misc & 0x7800) >> 11
        self.AA = (misc & 0x0400) != 0
        self.TC = (misc & 0x200) != 0
        self.RD = (misc & 0x100) != 0
        self.RA = (misc & 0x80) != 0
        self.Z = (misc & 0x70) >> 4  # Never used
        self.RCode = misc & 0xF

    def __str__(self):
        return '<DNSHeader {}>'.format(str(self.__dict__))


class DNSQuestion:
    def __init__(self, domain, qtype, qclass):
        self.domain = domain
        self.qtype = qtype
        self.qclass = qclass

    def __str__(self):
        return '<DNSQuestion {}>'.format(str(self.__dict__))

    def __hash__(self):
        return hash((self.domain, self.qtype, self.qclass))

    def __eq__(self, other):
        return (self.domain, self.qtype, self.qclass) == (other.domain, other.qtype, other.qclass)


class ResourceRecord:
    def __init__(self, name_bytes: bytes, qtype: QType, qclass: QClass, ttl: int, data: bytes):
        self.name_bytes = name_bytes
        self.qtype = qtype
        self.qclass = qclass
        self.ttl = ttl
        self.data = data


def parse_query(data: bytes) -> Tuple[DNSHeader, List[DNSQuestion], bytes]:
    header = DNSHeader()
    header.parse_header(data[0:12])

    body = data[12:]
    offset, questions = parse_questions(header.QDCount, body)

    if len(data) != 12 + offset:
        print("data length:", len(data), "  handled length", 12 + offset)

    return header, questions, body[:offset]


def parse_questions(size: int, body: bytes) -> Tuple[int, List[DNSQuestion]]:
    offset = 0
    questions = []
    for _ in range(size):
        offset, question = parse_question(offset, body)
        questions.append(question)
    return offset, questions


def parse_question(offset: int, body: bytes) -> Tuple[int, DNSQuestion]:
    domain = ""
    length = body[offset]
    while True:
        offset += 1
        domain += body[offset: offset + length].decode()
        offset += length
        length = body[offset]
        if length != 0:
            domain += '.'
        else:
            break
    offset += 1
    qtype = int.from_bytes(body[offset:offset + 2], byteorder='big')
    offset += 2
    qclass = int.from_bytes(body[offset:offset + 2], byteorder='big')
    offset += 2

    res = DNSQuestion(domain, QType(qtype), QClass(qclass))
    print(res)

    return offset, res


cache: Dict[DNSQuestion, List[Tuple[int, ResourceRecord]]] = defaultdict(list)


def handle(question: DNSQuestion) -> List[ResourceRecord]:
    now = int(datetime.now().timestamp())

    should_send_query = len(cache[question]) == 0
    should_send_query = should_send_query or any(now - time >= record.ttl for time, record in cache[question])

    if should_send_query:
        print("There are {} records in cache. Now should update cache".format(len(cache[question])))
        cache[question].extend([(now, record) for record in send_query(question)])

    cache[question] = list(filter(lambda item: now - item[0] < item[1].ttl, cache[question]))

    result = []
    for time, record in cache[question]:
        record_copy = copy.deepcopy(record)
        record_copy.ttl -= now - time
        result.append(record_copy)

    return result


def send_query(question: DNSQuestion) -> List[ResourceRecord]:
    print("Updating records")
    answer: Answer
    try:
        answer = query(question.domain, question.qtype.value, question.qclass.value)
    except NoAnswer:
        print("NoAnswer")
        return []

    result = []
    for it in answer.response.answer:

        def label_to_bytes(labels: Tuple[bytes]) -> bytes:
            label_data = b''
            for label in labels:
                label_data += int.to_bytes(len(label), 1, byteorder='big')
                label_data += label
            return label_data

        name_bytes = label_to_bytes(it.name)
        ttl = it.ttl
        qtype = QType(it.rdtype)
        qclass = QClass(it.rdclass)

        for item in it.items:
            data: bytes = b''

            if qtype == QType.A:
                data = ipaddress.ip_address(item.address).packed
            elif qtype == QType.NS:
                data = label_to_bytes(item.target.labels)
            elif qtype == QType.CNAME:
                data = label_to_bytes(item.target.labels)
            elif qtype == QType.MX:
                data = int.to_bytes(item.preference, 2, byteorder='big')
                data += label_to_bytes(item.exchange.labels)
            elif qtype == QType.TXT:
                data = b''
                for string in item.strings:
                    data += int.to_bytes(len(string), 1, byteorder='big')
                    data += string
                pass
            elif qtype == QType.AAAA:
                data = ipaddress.ip_address(item.address).packed

            rdata = ResourceRecord(name_bytes, qtype, qclass, ttl, data)
            result.append(rdata)

    return result


def write(header: DNSHeader, questions_bytes: bytes, questions: List[DNSQuestion],
          responds: List[ResourceRecord]) -> bytes:
    data = b''
    data += header.data[0:2]
    data += int.to_bytes(header.data[2] | 0x80, 1, byteorder='big')
    data += int.to_bytes(header.data[3], 1, byteorder='big')
    data += header.data[4:6]
    data += int.to_bytes(len(responds), 2, byteorder='big')
    data += int.to_bytes(0, 2, byteorder='big')
    data += int.to_bytes(0, 2, byteorder='big')
    data += questions_bytes

    for record in responds:
        data += record.name_bytes
        data += int.to_bytes(record.qtype.value, 2, byteorder='big')
        data += int.to_bytes(record.qclass.value, 2, byteorder='big')
        data += int.to_bytes(record.ttl, 4, byteorder='big')
        data += int.to_bytes(len(record.data), 2, byteorder='big')
        data += record.data

    return data


class DNSServerProtocol(asyncio.Protocol):

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        header, questions, questions_bytes = parse_query(data)
        responds = []
        for question in questions:
            responds += handle(question)
        to_write = write(header, questions_bytes, questions, responds)
        self.transport.sendto(to_write, addr)


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    coro = loop.create_datagram_endpoint(
        lambda: DNSServerProtocol(), local_addr=('0.0.0.0', 9090)
    )
    transport, protocol = loop.run_until_complete(coro)

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    transport.close()
    loop.close()

from typing import Optional

from twisted.internet.endpoints import _WrappingProtocol
from twisted.internet.interfaces import IProtocol
from zope.interface import Interface, implementer


class IAddress(Interface):
    pass


@implementer(IAddress)
class DummyAddress:
    pass


dummy_address = DummyAddress()


class IProtocolFactory(Interface):
    def buildProtocol(addr: IAddress) -> Optional[IProtocol]:
        pass

def _make_connection(
    client_factory: IProtocolFactory,
    server_factory: IProtocolFactory,
) -> None:
    server_protocol = server_factory.buildProtocol(dummy_address)
    assert server_protocol is not None

    client_protocol = client_factory.buildProtocol(dummy_address)
    assert isinstance(client_protocol, _WrappingProtocol)
    print("Hello")

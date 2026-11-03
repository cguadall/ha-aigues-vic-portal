"""Errors raised by the Aigües Vic client."""


class AiguesVicError(Exception):
    """Base error for the Aigües Vic client."""


class CannotConnectError(AiguesVicError):
    """The portal could not be reached or returned a server error."""


class InvalidAuthError(AiguesVicError):
    """The portal rejected the credentials."""


class RateLimitedError(AiguesVicError):
    """The portal asked the client to slow down."""


class ParseError(AiguesVicError):
    """A page did not have the expected structure."""


class ContractNotFoundError(AiguesVicError):
    """The requested contract is not available in the account."""


class SessionExpiredError(AiguesVicError):
    """The portal session expired; the client logs in again."""

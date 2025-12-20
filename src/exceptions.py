"""Custom exceptions for prediction market clients."""


class ClientError(Exception):
    """Base exception for all client errors."""
    pass


class ConnectionError(ClientError):
    """Failed to connect or authenticate with the platform."""
    pass


class NotConnectedError(ClientError):
    """Operation attempted before calling connect()."""
    pass


class OrderError(ClientError):
    """Base exception for order-related errors."""
    pass


class OrderNotFoundError(OrderError):
    """Order does not exist or has already been cancelled/filled."""

    def __init__(self, order_id: str):
        self.order_id = order_id
        super().__init__(f"Order not found: {order_id}")


class InsufficientBalanceError(OrderError):
    """Not enough balance to place the order."""

    def __init__(self, required: float = 0, available: float = 0):
        self.required = required
        self.available = available
        super().__init__(
            f"Insufficient balance: required {required}, available {available}"
        )


class OrderRejectedError(OrderError):
    """Order was rejected by the exchange."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Order rejected: {reason}")


class RateLimitError(ClientError):
    """Rate limit exceeded."""

    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        msg = "Rate limit exceeded"
        if retry_after:
            msg += f", retry after {retry_after}s"
        super().__init__(msg)

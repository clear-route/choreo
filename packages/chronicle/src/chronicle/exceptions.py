"""Domain exceptions for Chronicle."""


class ReportValidationError(Exception):
    """Raised when a report fails JSON Schema validation."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("; ".join(messages))


class TooManyConnections(Exception):
    """Raised when the SSE connection limit is reached."""

    pass

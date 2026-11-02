class ToolError(Exception):
    """Base class for failures a real remote tool could raise."""


class ToolTimeout(ToolError):
    """No answer in time. The side effect may or may not have happened."""


class ToolUnavailable(ToolError):
    """Transient 503. Safe to retry with the same idempotency key."""

    status_code = 503

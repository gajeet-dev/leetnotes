class LeetCodeError(Exception):
    """Base class for everything this package raises."""


class TransportError(LeetCodeError):
    """Network failure, timeout, or unexpected HTTP status."""


class AuthError(LeetCodeError):
    """Session cookie / CSRF token missing, invalid, or expired."""


class RateLimitError(LeetCodeError):
    """Still rate limited after retries."""


class NotFoundError(LeetCodeError):
    """The requested problem/submission doesn't exist."""


class ResponseShapeError(LeetCodeError):
    """Response wasn't valid JSON or lacked the expected structure."""


class NotConfigured(LeetCodeError):
    """The LeetCode cookies haven't been set up yet."""

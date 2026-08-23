"""Domain-specific exceptions raised by the self-healing workflow."""


class AutoHealError(RuntimeError):
    """Base exception for expected AutoHeal failures."""


class PatchApplicationError(AutoHealError):
    """Raised when a generated patch cannot be applied."""


class ValidationTimeoutError(AutoHealError):
    """Raised when local validation exceeds its time limit."""


class LLMResponseParsingError(AutoHealError):
    """Raised when an LLM response is not in the expected format."""

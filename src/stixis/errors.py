"""Exceptions raised by Stixis during CSS tokenization, validation, and serialization."""

from __future__ import annotations

from typing import Any


class StixisError(ValueError):
    """Base exception for all Stixis errors."""


class StixisSyntaxError(StixisError):
    """Raised when CSS syntax or template tokenization is malformed."""


class StixisValidationError(StixisError):
    """Raised when a value or CSS token stream violates the target Value Definition Syntax."""

    def __init__(
        self,
        target: str,
        expected_syntax: str,
        invalid_value: Any,
        message: str | None = None,
    ) -> None:
        self.target = target
        self.expected_syntax = expected_syntax
        self.invalid_value = invalid_value
        if message is None:
            message = (
                f"Invalid value for {target!r}: {invalid_value!r} "
                f"(expected syntax: {expected_syntax})"
            )
        super().__init__(message)

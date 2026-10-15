"""Changing one's own password while signed in (admins and customers; ADR-0023).

Both sides check the current password first (a wrong one counts as a failed
confirmation, `stepup.count_failure`, so a stolen session can't guess it), then the
new one against Django's validators (12 characters, not common, not only digits, not
like the account's names), then set it, audit it without values, end every other
sign-in and email the owner of the account (`password_changed`).
"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.core.errors import ErrorCode, ProblemError, field_error

__all__ = ["check_new_password", "notify_changed", "wrong_current"]


def wrong_current() -> ProblemError:
    message = "The current password is not right."
    return ProblemError(
        ErrorCode.VALIDATION_ERROR,
        message,
        field_errors={"current_password": [field_error(message, code="wrong_password")]},
    )


def check_new_password(user: User, current: str, new: str) -> None:
    """VALIDATION_ERROR on `new_password` with each validator's code."""
    if new == current:
        message = "Choose a new password, not the current one."
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            message,
            field_errors={"new_password": [field_error(message, code="password_unchanged")]},
        )
    try:
        validate_password(new, user)
    except ValidationError as exc:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            exc.messages[0],
            field_errors={
                "new_password": [
                    field_error(error.messages[0], code=error.code or "invalid")
                    for error in exc.error_list
                ]
            },
        ) from None


def notify_changed(user: User) -> None:
    """Email the account's owner (queued; sent after the commit)."""
    from apps.notifications import services as notifications  # noqa: PLC0415

    if not user.email:
        return
    notifications.enqueue(
        user,
        "password_changed",
        {"changed_at": notifications.local_time(timezone.now(), user)},
    )

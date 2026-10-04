"""`field_error_codes` mirrors `field_errors` with a stable code per message (ADR-0015)."""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions, serializers

from apps.core.errors import (
    DEFAULT_FIELD_ERROR_CODE,
    ErrorCode,
    Problem,
    ProblemError,
    field_error,
    flatten_error_codes,
    problem_exception_handler,
)


def handle(exc: Exception) -> Any:
    response = problem_exception_handler(exc, {})
    assert response is not None
    return response.data


def test_problem_errors_carry_the_codes_of_their_messages() -> None:
    data = handle(
        ProblemError(
            ErrorCode.VALIDATION_ERROR,
            field_errors={
                "username": [field_error("This username is already taken.", code="username_taken")],
                "password": [
                    field_error("Use 8 to 64 characters.", code="password_length"),
                    "A plain message.",
                ],
            },
        )
    )
    assert data["field_errors"] == {
        "username": ["This username is already taken."],
        "password": ["Use 8 to 64 characters.", "A plain message."],
    }
    assert data["field_error_codes"] == {
        "username": ["username_taken"],
        "password": ["password_length", DEFAULT_FIELD_ERROR_CODE],
    }


class _Nested(serializers.Serializer[Any]):
    max_streams = serializers.IntegerField(min_value=1, max_value=10)


class _Form(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=5)
    email = serializers.EmailField()
    locale = serializers.ChoiceField(choices=["ar", "en"])
    access = _Nested()


def test_serializer_validation_errors_keep_drf_codes_aligned_with_messages() -> None:
    form = _Form(data={"name": "too long", "locale": "fr", "access": {"max_streams": 0}})
    assert not form.is_valid()
    data = handle(exceptions.ValidationError(form.errors))
    assert set(data["field_errors"]) == set(data["field_error_codes"])
    assert data["field_error_codes"] == {
        "name": ["max_length"],
        "email": ["required"],
        "locale": ["invalid_choice"],
        "access.max_streams": ["min_value"],
    }
    for name, messages in data["field_errors"].items():
        assert len(messages) == len(data["field_error_codes"][name])


def test_django_validation_errors_keep_their_codes() -> None:
    by_field = handle(
        DjangoValidationError(
            {
                "phone": [
                    DjangoValidationError("Enter a valid phone number.", code="invalid_phone")
                ],
                "__all__": ["Mismatch."],
            }
        )
    )
    assert by_field["field_error_codes"] == {
        "phone": ["invalid_phone"],
        "non_field_errors": [DEFAULT_FIELD_ERROR_CODE],
    }
    plain = handle(
        DjangoValidationError(
            [DjangoValidationError("One.", code="one"), DjangoValidationError("Two.")]
        )
    )
    assert plain["field_errors"] == {"non_field_errors": ["One.", "Two."]}
    assert plain["field_error_codes"] == {"non_field_errors": ["one", DEFAULT_FIELD_ERROR_CODE]}


def test_structured_detail_on_other_errors_gets_codes_too() -> None:
    data = handle(exceptions.ParseError({"body": ["Malformed JSON."]}))
    assert data["field_error_codes"] == {"body": ["parse_error"]}


def test_no_field_errors_means_no_codes() -> None:
    data = handle(ProblemError(ErrorCode.CONFLICT, "Already ended."))
    assert "field_errors" not in data
    assert "field_error_codes" not in data


def test_codes_always_align_with_messages() -> None:
    problem = Problem(
        ErrorCode.VALIDATION_ERROR,
        400,
        "Invalid input.",
        {"a": ["x", "y"], "b": ["z"]},
        field_error_codes={"a": ["first"], "b": ["one", "extra"]},
    )
    assert problem.body()["field_error_codes"] == {
        "a": ["first", DEFAULT_FIELD_ERROR_CODE],
        "b": ["one"],
    }


def test_flatten_error_codes_walks_nested_structures() -> None:
    detail = {
        "devices": [{}, {"name": [exceptions.ErrorDetail("Required.", code="required")]}],
        "tags": {0: ["Plain."]},
    }
    assert flatten_error_codes(detail) == {
        "devices.1.name": ["required"],
        "tags.0": [DEFAULT_FIELD_ERROR_CODE],
    }
    assert flatten_error_codes(exceptions.ErrorDetail("Nope.", code="nope")) == {
        "non_field_errors": ["nope"]
    }

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Users
from app.services.auth_service import (
    INVALID_CREDENTIALS_MESSAGE,
    AuthError,
    InvalidCredentials,
    sign_in,
)
from app.utils.security import hash_password

TEST_PASSWORD = "customer123"


def _fresh_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    session.add(
        Users(
            full_name="Carlo Reyes",
            email="carlo.cust@rental.ph",
            phone="09175551234",
            password_hash=hash_password(TEST_PASSWORD),
            address="789 Mabini Ave, Pasig",
            license_number="D02-5555-000",
            license_expiry=date(2029, 3, 22),
            role="customer",
            created_at=datetime.now(),
        )
    )
    session.commit()
    return session


def test_sign_in_success() -> None:
    session = _fresh_session()
    try:
        user = sign_in(session, "carlo.cust@rental.ph", TEST_PASSWORD)
        assert user.user_id is not None
        assert user.email == "carlo.cust@rental.ph"
        assert user.role == "customer"
        print(f"  signed in as {user.full_name} ({user.role}), id={user.user_id}")
    finally:
        session.close()


def test_email_is_normalized() -> None:
    session = _fresh_session()
    try:
        for variant in ("CARLO.CUST@RENTAL.PH", "  carlo.cust@rental.ph  ", "Carlo.Cust@Rental.Ph"):
            user = sign_in(session, variant, TEST_PASSWORD)
            assert user.email == "carlo.cust@rental.ph"
        print(f"  matched {3} case/whitespace variants of the same address")
    finally:
        session.close()


def test_wrong_password() -> None:
    session = _fresh_session()
    try:
        sign_in(session, "carlo.cust@rental.ph", "wrongpassword")
        raise AssertionError("wrong password should have raised")
    except InvalidCredentials as exc:
        assert str(exc) == INVALID_CREDENTIALS_MESSAGE
        print(f"  blocked wrong password: {exc}")
    finally:
        session.close()


def test_unknown_email_is_indistinguishable() -> None:
    session = _fresh_session()
    try:
        try:
            sign_in(session, "nobody@rental.ph", TEST_PASSWORD)
            raise AssertionError("unknown email should have raised")
        except InvalidCredentials as exc:
            unknown_message = str(exc)

        assert unknown_message == INVALID_CREDENTIALS_MESSAGE
        print(f"  unknown email returns the identical message: {unknown_message}")
    finally:
        session.close()


def test_empty_and_oversized_input() -> None:
    session = _fresh_session()
    try:
        for email, password in (
            ("", TEST_PASSWORD),
            ("   ", TEST_PASSWORD),
            ("carlo.cust@rental.ph", ""),
            ("carlo.cust@rental.ph", "a" * 73),
        ):
            try:
                sign_in(session, email, password)
                raise AssertionError(f"expected failure for {email!r}/{password[:12]!r}")
            except InvalidCredentials:
                pass
        print("  blocked empty email, empty password, and 73-byte password")
    finally:
        session.close()


def test_invalid_credentials_is_auth_error() -> None:
    assert issubclass(InvalidCredentials, AuthError)
    print("  InvalidCredentials subclasses AuthError")


if __name__ == "__main__":
    print("== sign in ==")
    test_sign_in_success()
    print("\n== email normalization ==")
    test_email_is_normalized()
    print("\n== wrong password ==")
    test_wrong_password()
    print("\n== unknown email ==")
    test_unknown_email_is_indistinguishable()
    print("\n== empty / oversized input ==")
    test_empty_and_oversized_input()
    print("\n== exception hierarchy ==")
    test_invalid_credentials_is_auth_error()
    print("\nALL AUTH TESTS PASSED")

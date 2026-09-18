import bcrypt

MAX_PASSWORD_BYTES = 72


def hash_password(plain: str) -> str:
    if len(plain.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if len(plain.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
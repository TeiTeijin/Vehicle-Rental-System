import app.models  # noqa: F401 - registers all tables on Base.metadata
from app.database import Base, engine


def main() -> None:
    Base.metadata.create_all(engine)
    print("Database tables created.")


if __name__ == "__main__":
    main()
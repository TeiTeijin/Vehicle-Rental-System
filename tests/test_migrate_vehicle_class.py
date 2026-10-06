"""Tests for the vehicle_class / engine_cc migration.

The database that needs this migration is the Aiven MySQL instance, which these
tests must not touch. So each test builds a deliberately *old* VEHICLE table by
hand -- the shape it had before the New Rental filter rail -- runs the migration
against that engine, and checks the result.

Building the old schema literally is the point. A test that started from
`Base.metadata.create_all` would already have both new columns, the migration
would do nothing, and the backfill rules would never be exercised at all.

The migration takes its target from `--database` rather than importing the
project's module-level engine, which is what makes these tests possible: the
engine is injected, so nothing in the suite can reach a real database by
accident.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from sqlalchemy import create_engine, inspect, text

OLD_VEHICLE = """
CREATE TABLE VEHICLE_CATEGORY (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_name VARCHAR(255) NOT NULL,
    base_rate_multiplier DECIMAL(10,2) NOT NULL
)
"""

OLD_VEHICLE_TABLE = """
CREATE TABLE VEHICLE (
    vehicle_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    make VARCHAR(255) NOT NULL,
    model VARCHAR(255) NOT NULL,
    year INTEGER NOT NULL,
    plate_number VARCHAR(7) NOT NULL UNIQUE,
    daily_rate DECIMAL(10,2) NOT NULL,
    mileage INTEGER NOT NULL,
    seats INTEGER,
    transmission VARCHAR(20),
    fuel_type VARCHAR(20),
    body_style VARCHAR(20),
    status VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL
)
"""

#: (plate, body_style, model). Covers every mapped body style, plus the cases that
#: must be left alone: an unknown style and a car whose model name is full of
#: digits that are not displacement.
OLD_ROWS = [
    ("OLD-001", "Sedan", "Vios"),
    ("OLD-002", "Hatchback", "Mirage"),
    ("OLD-003", "MPV", "Xpander"),
    ("OLD-004", "SUV", "Fortuner"),
    ("OLD-005", "Pickup", "D-Max"),
    ("OLD-006", "Underbone", "Click 125i"),
    ("OLD-007", "Scooter", "NMAX 155"),
    ("OLD-008", "Underbone", "Ninja 400"),
    ("OLD-009", "Scooter", "P200"),
    # Left NULL on purpose.
    ("OLD-010", "Convertible", "Mustang GT"),
    ("OLD-011", "Sedan", "Model 3"),
    ("OLD-012", None, "Unclassified"),
    # A motorcycle with no plausible cc in its name: FZ-S is a real bike with no
    # number in the model, and guessing from the letters would be inventing data.
    ("OLD-013", "Underbone", "FZ-S"),
]


def _seed_old_fleet(conn) -> None:
    conn.execute(text(OLD_VEHICLE))
    conn.execute(text(OLD_VEHICLE_TABLE))
    conn.execute(
        text(
            "INSERT INTO VEHICLE_CATEGORY (category_name, base_rate_multiplier) "
            "VALUES ('Car', 1.0)"
        )
    )
    for plate, body, model in OLD_ROWS:
        conn.execute(
            text(
                "INSERT INTO VEHICLE (category_id, make, model, year, plate_number, "
                "daily_rate, mileage, seats, status, body_style, created_at) "
                "VALUES (1, 'Test', :model, 2022, :plate, 2500, 1000, 5, "
                "'available', :body, '2026-01-01')"
            ),
            {"plate": plate, "body": body, "model": model},
        )


@pytest.fixture
def old_db(tmp_path):
    """An old-schema database, plus a runner for the migration against it."""
    db_file = tmp_path / "legacy_vehicles.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", future=True)
    with engine.begin() as conn:
        _seed_old_fleet(conn)

    migration = importlib.import_module("scripts.migrate_vehicle_class")

    def run(*args):
        sys.argv = ["migrate_vehicle_class", "--database", str(db_file), *args]
        try:
            migration.main()
        finally:
            sys.argv = ["pytest"]
        return engine

    try:
        yield engine, run
    finally:
        migration.engine = None
        engine.dispose()


def columns(engine, table="VEHICLE") -> dict[str, dict]:
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


def rows(engine) -> dict[str, tuple]:
    with engine.connect() as conn:
        return {
            plate: (klass, cc)
            for plate, klass, cc in conn.execute(
                text(
                    "SELECT plate_number, vehicle_class, engine_cc "
                    "FROM VEHICLE ORDER BY plate_number"
                )
            )
        }


class TestAddsColumns:
    def test_adds_both_columns(self, old_db):
        engine, run = old_db
        run()
        cols = columns(engine)
        assert "vehicle_class" in cols, "VEHICLE.vehicle_class was not added"
        assert "engine_cc" in cols, "VEHICLE.engine_cc was not added"

    def test_new_columns_are_nullable(self, old_db):
        engine, run = old_db
        run()
        for name in ("vehicle_class", "engine_cc"):
            assert columns(engine)[name]["nullable"] is True

    def test_existing_rows_survive(self, old_db):
        engine, run = old_db
        run()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM VEHICLE")).scalar_one() == len(
                OLD_ROWS
            )
            assert (
                conn.execute(text("SELECT mileage FROM VEHICLE WHERE plate_number = 'OLD-001'"))
                .scalar_one()
                == 1000
            )


class TestBackfills:
    def test_maps_every_known_body_style(self, old_db):
        engine, run = old_db
        run()
        stored = rows(engine)
        assert stored["OLD-001"][0] == "small"       # Sedan
        assert stored["OLD-002"][0] == "small"       # Hatchback
        assert stored["OLD-003"][0] == "van"         # MPV
        assert stored["OLD-004"][0] == "suv"         # SUV
        assert stored["OLD-005"][0] == "pickup"      # Pickup
        assert stored["OLD-006"][0] == "motorcycle"  # Underbone
        assert stored["OLD-007"][0] == "motorcycle"  # Scooter

    def test_leaves_an_unknown_body_style_alone(self, old_db):
        engine, run = old_db
        run()
        # "Convertible" is not in the mapping, and inventing a class for it would
        # be a guess about a vehicle nobody has seen.
        assert rows(engine)["OLD-010"][0] is None

    def test_leaves_a_missing_body_style_alone(self, old_db):
        engine, run = old_db
        run()
        assert rows(engine)["OLD-012"][0] is None

    def test_parses_displacement_out_of_the_model_name(self, old_db):
        engine, run = old_db
        run()
        stored = rows(engine)
        assert stored["OLD-006"][1] == 125   # Click 125i
        assert stored["OLD-007"][1] == 155   # NMAX 155
        assert stored["OLD-008"][1] == 400   # Ninja 400
        assert stored["OLD-009"][1] == 200   # P200

    def test_does_not_invent_displacement_for_a_car(self, old_db):
        engine, run = old_db
        run()
        # "Model 3" is a car. Its name has a digit, and reading it as a 3cc
        # engine would be nonsense.
        assert rows(engine)["OLD-011"][1] is None

    def test_does_not_invent_displacement_without_a_number(self, old_db):
        engine, run = old_db
        run()
        assert rows(engine)["OLD-013"][0] == "motorcycle"
        assert rows(engine)["OLD-013"][1] is None

    def test_never_assigns_medium_or_truck(self, old_db):
        """Nothing in an old fleet is a medium car or a truck.

        Inventing those here would put fiction into a real database; they come
        from the demo seeder instead.
        """
        engine, run = old_db
        run()
        with engine.connect() as conn:
            assigned = {
                value
                for (value,) in conn.execute(
                    text(
                        "SELECT DISTINCT vehicle_class FROM VEHICLE "
                        "WHERE vehicle_class IS NOT NULL"
                    )
                )
            }
        assert "medium" not in assigned
        assert "truck" not in assigned


class TestParseCc:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("Click 125i", 125),
            ("NMAX 155", 155),
            ("Ninja 400", 400),
            ("P200", 200),
            ("NMAX 155 Special 2", 155),   # last plausible number wins
            ("Model 3", None),              # below MIN_CC, ignored
            ("Big 9000", None),             # above MAX_CC, ignored
            ("FZ-S", None),
            ("", None),
            (None, None),
        ],
    )
    def test_parse(self, model, expected):
        from scripts.migrate_vehicle_class import parse_cc

        assert parse_cc(model) == expected


class TestIdempotent:
    def test_running_twice_changes_nothing_the_second_time(self, old_db):
        engine, run = old_db
        run()
        first = rows(engine)
        run()
        assert rows(engine) == first

    def test_a_hand_corrected_row_is_not_overwritten(self, old_db):
        engine, run = old_db
        run()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE VEHICLE SET vehicle_class = 'truck', engine_cc = 999 "
                    "WHERE plate_number = 'OLD-001'"
                )
            )
        run()
        assert rows(engine)["OLD-001"] == ("truck", 999)

    def test_reports_a_missing_table_and_exits_nonzero(self, tmp_path):
        """No VEHICLE table is a clear message and exit 1, not a traceback.

        Exiting nonzero matters: a script that "succeeded" against a database it
        never migrated would let a pipeline carry on to the next step against a
        schema that is still wrong.
        """
        db_file = tmp_path / "empty.db"
        engine = create_engine(f"sqlite:///{db_file.as_posix()}", future=True)
        migration = importlib.import_module("scripts.migrate_vehicle_class")
        migration.engine = engine
        saved = sys.argv
        try:
            sys.argv = ["migrate_vehicle_class", "--database", str(db_file)]
            try:
                with pytest.raises(SystemExit) as caught:
                    migration.main()
            finally:
                sys.argv = saved
        finally:
            migration.engine = None
            engine.dispose()
        assert caught.value.code == 1
        assert inspect(engine).get_table_names() == []


class TestDryRun:
    def test_changes_nothing(self, old_db):
        engine, run = old_db
        # Column *names* rather than the full descriptors: SQLAlchemy builds a
        # fresh type object per inspection, so two dicts of an unchanged table
        # never compare equal.
        before = set(columns(engine))
        run("--dry-run")
        assert set(columns(engine)) == before
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM VEHICLE")).scalar_one() == len(
                OLD_ROWS
            )

    def test_reports_the_columns_it_would_add(self, old_db, capsys):
        engine, run = old_db
        run("--dry-run")
        out = capsys.readouterr().out
        assert "WOULD ADD" in out
        assert "vehicle_class" in out
        assert "engine_cc" in out

    def test_reports_the_backfill_it_would_do(self, old_db, capsys):
        """The dry run's whole purpose: what would change, without changing it.

        It has to plan against the schema the real run would produce, so the counts
        come from the same rules a real run uses: 11 of the 13 rows have a mapped
        body style, and 4 of the 6 motorcycles carry a plausible cc in their name
        (FZ-S does not).
        """
        engine, run = old_db
        run("--dry-run")
        out = capsys.readouterr().out
        assert "would backfill vehicle_class on 11 row(s)" in out
        assert "would backfill engine_cc on 4 row(s)" in out

    def test_the_reported_plan_matches_what_a_real_run_does(self, old_db, capsys):
        engine, run = old_db
        run("--dry-run")
        dry = capsys.readouterr().out
        run()
        real = rows(engine)

        planned_classes = {
            plate for plate, (klass, _) in real.items() if klass is not None
        }
        assert f"would backfill vehicle_class on {len(planned_classes)} row(s)" in dry
        planned_cc = sum(1 for _, cc in real.values() if cc is not None)
        assert f"would backfill engine_cc on {planned_cc} row(s)" in dry


class TestTargetResolution:
    def test_a_bare_path_becomes_sqlite(self):
        from scripts.migrate_vehicle_class import resolve_url

        assert resolve_url("demo.db") == "sqlite:///demo.db"

    def test_a_url_is_passed_through_untouched(self):
        from scripts.migrate_vehicle_class import resolve_url

        url = "mysql+pymysql://user:pw@host:3306/vehicle_rental"
        assert resolve_url(url) == url

    def test_the_password_is_not_printed(self):
        from scripts.migrate_vehicle_class import describe

        printed = describe("mysql+pymysql://user:hunter2@host:3306/vehicle_rental")
        assert "hunter2" not in printed
        assert "host" in printed

    def test_database_is_required(self):
        """There is no default target, on purpose.

        The project default is DATABASE_URL, the live Aiven instance, so a
        migration that ran because nobody passed a flag would be a production
        schema change nobody asked for.
        """
        migration = importlib.import_module("scripts.migrate_vehicle_class")
        saved = sys.argv
        sys.argv = ["migrate_vehicle_class"]
        try:
            with pytest.raises(SystemExit) as caught:
                migration.main()
        finally:
            sys.argv = saved
        assert caught.value.code != 0
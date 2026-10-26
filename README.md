# RentWheels Vehicle Rental System

RentWheels is a desktop vehicle-rental management system built with Python, PySide6, and SQLAlchemy. It combines a customer-facing booking app with a staff operations app on one shared database.

The customer app covers discovery, vehicle details, online booking, payment selection, and account history. The staff app covers counter rentals, bookings, fleet and rate management, customers, payments and deposits, inspections, maintenance, reports, and settings.

## Features

### Customer app

- Account signup, sign-in, and password reset
- Browse, search, and filter the vehicle catalog
- Vehicle details with cached photos and optional 3D media
- Date/time and pickup/return branch selection
- Online checkout with cash, card, or GCash
- Long-term rental discounts and motorcycle pricing from shared settings
- Bookings, payment history, notifications, and profile management

### Staff app

- Role-gated staff/admin sign-in and first-run admin setup
- Dashboard and reports with CSV/PDF export
- Counter rental wizard
- Online and walk-in booking management
- Confirm, cancel, extend, check in, and check out
- Rental payments, deposits, refunds, and settlement
- Fleet, vehicle rates, maintenance, and retirement
- Customer management, notes, flags, and licence documents
- Inspection records
- Shared pricing, receipt, and operational settings
- Sanitized SQLite backups and media cache controls

## Tech stack

- Python 3.13+ (CI uses 3.13)
- PySide6 6.11
- SQLAlchemy 2.0
- SQLite for local development and tests; MySQL/PyMySQL supported
- pytest and pytest-xdist
- Ruff for linting

## Project layout

```text
app/
  domain/        Pure pricing, vehicle-type, and rental rules
  models/        SQLAlchemy models
  services/      Business logic and the only database write path
  ui/            Customer PySide6 application
  staff/         Staff PySide6 application
  utils/         Money, security, file, and font helpers
scripts/         Database initialization, migrations, seeds, and diagnostics
tests/           Unit, service, migration, security, and GUI tests
docs/            Audit reports, plans, and operational notes
```

The intended dependency direction is:

```text
models -> services -> UI
            ^
          domain
```

All money is handled with `Decimal`. Database writes belong in `app/services`; UI code should call services rather than write rows directly.

## Getting started

### 1. Create a virtual environment

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

### 2. Configure the database

Create a `.env` file in the repository root. The simplest local setup is SQLite:

```dotenv
DATABASE_URL=sqlite:///rental.db
```

For MySQL, use a URL such as:

```dotenv
DATABASE_URL=mysql+pymysql://user:password@host:3306/rentwheels?ssl=ca.pem
```

The `ssl` query parameter is converted to `ssl_ca` and resolved relative to the repository root. Keep `.env`, database files, `ca.pem`, and customer documents out of version control; they are already ignored.

The CarImages API is optional. If media is unavailable, the UI falls back to placeholders:

```dotenv
CI_API_KEY=
CI_API_SECRET=
```

### 3. Initialize the schema

For an empty database:

```bash
python -m scripts.init_db
```

For an existing database, run the migrations in the documented order:

```bash
python -m scripts.migrate_staff_schema
python -m scripts.migrate_customer_booking
python -m scripts.migrate_guarantor_profile
```

Additional migrations under `scripts/migrate_*.py` cover settings, payment kinds, deposits, maintenance, vehicle management, media URLs, and indexes. Most accept `--dry-run`.

### 4. Seed a demo database

The demo seeder creates a self-contained `demo.db` with fictional customers, staff, vehicles, bookings, payments, inspections, and maintenance history:

```bash
python -m scripts.seed_demo_data --database demo.db
```

Add the customer catalog pieces (branches and a demo shopper) on top:

```bash
DATABASE_URL=sqlite:///demo.db python -m scripts.seed_customer_catalog --demo-customer
```

Windows PowerShell:

```powershell
$env:DATABASE_URL="sqlite:///demo.db"
python -m scripts.seed_customer_catalog --demo-customer
```

Every demo account uses the password `demo-password`.

| Role | Email |
|---|---|
| Admin | `admin@rentwheels.local` |
| Counter staff | `counter@rentwheels.local` |
| Demo customer | `demo@rentwheels.local` |

## Running the apps

### Customer app

With the default `.env` database:

```bash
python -m app.ui.main_window
```

Against the demo database:

```bash
DATABASE_URL=sqlite:///demo.db python -m app.ui.main_window
```

Windows PowerShell:

```powershell
$env:DATABASE_URL="sqlite:///demo.db"
python -m app.ui.main_window
```

### Staff app

Open the default demo database:

```bash
python -m app.staff --demo
```

Open a specific demo file:

```bash
python -m app.staff --demo-file demo.db
```

Open a local snapshot of the live database:

```bash
python -m app.staff --backup-file path/to/snapshot.db
```

## Tests

Run the full suite:

```bash
python -m pytest -q
```

On a headless machine, force Qt's offscreen platform:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

The suite uses throwaway SQLite databases and does not touch live MySQL data. GUI tests must run offscreen or on a real desktop; they are not safe to fork under xdist.

Lint:

```bash
python -m ruff check app tests
```

## Pricing and money rules

Shared pricing settings live in `APP_SETTINGS` and are read through `policy_from_settings`:

- Long-term car rental discount
- Motorcycle multiplier
- Deposit rules and payment defaults

The online checkout and counter rental paths both apply the same policy. Rental subtotals, fees, VAT, payments, deposits, refunds, and penalties are all `Decimal` values.

## Documentation

- [Final audit report](docs/audits/final-audit-report.md)
- [Final audit register](docs/audits/final-audit-register.md)
- [Final audit plan](docs/plans/2026-10-25-final-audit.md)
- [Backup security notes](docs/backup-security.md)
- [Release checks](docs/release-checks-2026-10-24.md)

## Operational notes

- Use a scratch SQLite database for demos and tests.
- Take a backup before applying migrations to a real database.
- The staff app can open a local backup snapshot without touching the live database.
- Customer licence documents and `.env` credentials are intentionally ignored by Git.
- CarImages failures are treated as environmental; the UI keeps working with cached media or placeholders.

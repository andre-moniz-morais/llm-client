"""Block until the database accepts a connection.

Used by the container entrypoint. ``manage.py check --database`` looks like it
would do this but never connects to PostgreSQL, so it passes against a database
that is not there and the failure only surfaces later, in ``migrate``.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, connections


class Command(BaseCommand):
    help = "Wait until the default database accepts connections."

    def add_arguments(self, parser):
        parser.add_argument("--tries", type=int, default=30)
        parser.add_argument("--delay", type=float, default=2.0)

    def handle(self, *args, tries: int, delay: float, **options):
        connection = connections["default"]
        for attempt in range(1, tries + 1):
            try:
                connection.ensure_connection()
            except OperationalError as exc:
                # Print the driver's reason every time: "could not translate
                # host name", "password authentication failed" and "database
                # does not exist" each need a different fix.
                reason = str(exc).strip().splitlines()[-1] if str(exc).strip() else repr(exc)
                self.stderr.write(f"Database not ready (attempt {attempt}/{tries}): {reason}")
                connection.close()
                if attempt < tries:
                    time.sleep(delay)
                continue
            self.stdout.write("Database is ready.")
            return
        raise CommandError(f"Database not reachable after {tries} attempts.")

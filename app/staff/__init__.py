"""Staff application.

A separate window from the customer app, sharing only the services and models.
Importing this package does not import Qt -- the pages do that -- so the
service-level modules stay testable headless.
"""

__all__ = ["context", "feedback", "theme"]

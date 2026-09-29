"""The staff pages.

One module per tab. Each exposes a ``build_*_page(shell)`` factory rather than
taking `shell` in `__init__`, so `PageSpec` can hold the factory and build
pages lazily -- a staff member never constructs the admin-only chart widgets.
"""

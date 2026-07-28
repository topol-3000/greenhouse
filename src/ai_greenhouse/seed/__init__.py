"""Repeatable, explicitly invoked data seeds built on the domain services.

Nothing in this package runs during application startup. Seeds open their own
transaction and use repositories only to resolve existing records; every write
goes through the same services used by the HTTP API.
"""

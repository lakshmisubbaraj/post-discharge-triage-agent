"""Shared extension instances.

Kept in their own module so they can be imported by both the app factory and
the models without creating circular imports.
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

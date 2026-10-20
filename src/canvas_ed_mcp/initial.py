"""Shared initial synchronization choice without config/sync import cycles."""

from enum import Enum


class InitialSyncMode(str, Enum):
    BASELINE_FROM_NOW = "baseline_from_now"
    IMPORT_ALL = "import_all"

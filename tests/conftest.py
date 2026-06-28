"""Pytest fixtures and configuration."""

import pytest


@pytest.fixture
def test_config():
    """Provide a test configuration."""
    return {
        "storage": {"backend": "memory"},
        "scheduler": {"default": "cpu"},
    }

"""
Временные файлы pytest хранятся внутри каталога артефактов проекта.
"""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    if config.option.basetemp is None:
        directory = config.rootpath / ".test-artifacts" / "pytest-tmp"
        directory.parent.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(directory)

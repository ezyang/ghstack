# mypy: ignore-errors

import pathlib
import runpy

import expecttest

import ghstack.test_prelude

import pytest


# Adapted from https://stackoverflow.com/questions/56807698/how-to-run-script-as-pytest-test


def pytest_collect_file(file_path: pathlib.Path, parent):
    # NB: script name must not end with py, due to doctest picking it
    # up in that case
    if file_path.suffixes == [".py", ".test"]:
        return Script.from_parent(parent, path=file_path)


class Script(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(self, name="default", direct=False)
        if self.path.parent.name in ["submit", "unlink"]:
            yield ScriptItem.from_parent(self, name="direct", direct=True)


class ScriptItem(pytest.Item):
    def __init__(self, *, direct, **kwargs):
        super().__init__(**kwargs)
        self.direct = direct

    def runtest(self):
        with ghstack.test_prelude.scoped_test(direct=self.direct):
            expecttest.EDIT_HISTORY.reload_file(self.fspath)
            runpy.run_path(self.fspath)

    def repr_failure(self, excinfo):
        excinfo.traceback = excinfo.traceback.cut(path=self.fspath)
        return super().repr_failure(excinfo)

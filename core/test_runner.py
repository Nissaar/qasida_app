"""
The test runner, with a record of every test's outcome for CI to report.

Django's runner prints failures and a total; the report posted on a pull
request needs each test's result, grouped by what it covers. When
TEST_RESULTS_JSON names a file, every test's class, name, outcome and
duration are written there once the run ends. Without it this is Django's
runner exactly.
"""

import json
import os
import time
import unittest

from django.test.runner import DiscoverRunner


class RecordingResult(unittest.TextTestResult):
    """A text result that also keeps one entry per test."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self._started = {}
        self.run_started = time.monotonic()

    def startTest(self, test):
        self._started[test.id()] = time.monotonic()
        super().startTest(test)

    def _record(self, test, outcome, detail=''):
        started = self._started.pop(test.id(), None)
        doc = (test.shortDescription() or '').strip()
        self.records.append({
            'id': test.id(),
            'class': type(test).__name__,
            'name': getattr(test, '_testMethodName', test.id()),
            'description': doc,
            'outcome': outcome,
            'seconds': round(time.monotonic() - started, 3) if started else None,
            'detail': detail[-2000:],
        })

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, 'passed')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, 'failed', self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, 'failed', self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, 'skipped', reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, 'passed')

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, 'failed', 'Expected to fail, but passed.')


class RecordingRunner(DiscoverRunner):
    def get_resultclass(self):
        if os.environ.get('TEST_RESULTS_JSON'):
            return RecordingResult
        return super().get_resultclass()

    def suite_result(self, suite, result, **kwargs):
        path = os.environ.get('TEST_RESULTS_JSON')
        if path and isinstance(result, RecordingResult):
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump({'tests': result.records, 'total': result.testsRun,
                           'seconds': round(time.monotonic() - result.run_started, 1)},
                          handle, indent=1)
        return super().suite_result(suite, result, **kwargs)

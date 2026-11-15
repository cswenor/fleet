"""Process-local GitHub cadence for a single supervisor serve invocation."""
from copy import deepcopy
import time


class Scheduler:
    def __init__(self, base):
        self.base = base
        self.interval = base
        self.due = None
        self.previous = {}
        self.metadata = {}
        self.timing = None
        self.error = None

    def collect(self, c, result, collector):
        """Reuse only in memory; timing always describes the last real attempt."""
        if self.due is not None and time.monotonic() < self.due:
            result.update(deepcopy(self.metadata))
            result['github_requests'] = {'requests': 0, 'not_modified': 0}
            result['github_collection'] = dict(self.timing)
            if self.error is not None:
                raise self.error.with_traceback(None)
            return deepcopy(self.previous)

        started = time.time()
        prior = self.timing
        data = None
        self.error = None
        try:
            data = collector(c, result, idle_previous=self.previous)
            return data
        except Exception as error:
            self.error = error
            raise
        finally:
            finished = time.time()
            self.interval = (min(self.interval * 2, max(self.base, 600))
                             if isinstance(data, dict) and data.get('idle') is True
                             and self.error is None else self.base)
            period = finished - started + self.interval
            if (prior is not None and 0 <= started - prior['finished_at'] <=
                    3 * prior['period_seconds'] + 15):
                period = max(period, finished - prior['finished_at'])
            self.timing = {'started_at': started, 'finished_at': finished,
                           'period_seconds': period, 'interval_seconds': self.interval,
                           'expected_next_at': finished + period,
                           'successful': isinstance(data, dict) and self.error is None}
            result['github_collection'] = dict(self.timing)
            self.due = time.monotonic() + self.interval
            self.previous = deepcopy(data) if isinstance(data, dict) and self.error is None else {}
            self.metadata = {key: deepcopy(result[key]) for key in
                             ('github_auth', 'github_budget') if key in result}

"""Normal-cadence probes with process-local idle enrichment backoff."""
from copy import deepcopy
import time


class Scheduler:
    def __init__(self, base):
        self.base = base
        self.interval = base
        self.due = None
        self.previous = {}
        self.timing = None
        self.full_finished_at = None

    def collect(self, c, result, collector):
        started = time.time()
        prior = self.timing
        full_due = self.due is None or time.monotonic() >= self.due
        data = None
        try:
            # Never defer discovery: a new PR resumes full collection this cycle.
            data = collector(c, result, idle_previous=self.previous, idle_full=full_due)
            return data
        finally:
            finished = time.time()
            monotonic = time.monotonic()
            healthy_idle = isinstance(data, dict) and data.get('idle') is True
            full = isinstance(data, dict) and data.get('full_collection') is True
            if not healthy_idle:
                self.interval = self.base
                self.due = monotonic + self.base
            elif full:
                self.interval = min(self.interval * 2, max(self.base, 600))
                self.due = monotonic + self.interval
            if full:
                self.full_finished_at = finished
            period = finished - started + self.base
            if (prior is not None and 0 <= started - prior['finished_at'] <=
                    3 * prior['period_seconds'] + 15):
                period = max(period, finished - prior['finished_at'])
            self.timing = {'started_at': started, 'finished_at': finished,
                           'period_seconds': period, 'interval_seconds': self.base,
                           'expected_next_at': finished + period,
                           'successful': isinstance(data, dict),
                           'full_interval_seconds': self.interval,
                           'full_finished_at': self.full_finished_at,
                           'full_expected_at': finished + max(0, self.due - monotonic)}
            result['github_collection'] = dict(self.timing)
            self.previous = deepcopy(data) if isinstance(data, dict) else {}

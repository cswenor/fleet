import contextlib
from datetime import datetime, timedelta
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fleet import state


class StateLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir()))
        self.addCleanup(self.temp.cleanup)
        self.c = {'_state': self.temp.name}
        self.name = 'threads-TASK-120'

    @contextlib.contextmanager
    def holder(self, name=None):
        script = '''
import sys
from fleet import state
with state.lock({'_state': sys.argv[1]}, sys.argv[2], operation='thread reply record'):
    print('ready', flush=True)
    sys.stdin.read()
'''
        with subprocess.Popen([sys.executable, '-c', script, self.temp.name,
                               name or self.name], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) as process:
            try:
                self.assertTrue(select.select([process.stdout], [], [], 5)[0],
                                'holder did not acquire its lock')
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                yield process
            finally:
                if not process.stdin.closed:
                    process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                self.assertEqual(process.returncode, 0)

    def test_thread_default_wait_times_out_with_live_holder_then_acquires_after_release(self):
        with self.holder() as holder:
            metadata = state.read(self.c, self.name + '-holder')
            self.assertEqual(metadata['pid'], holder.pid)
            self.assertEqual(metadata['op'], 'thread reply record')
            self.assertEqual(datetime.fromisoformat(metadata['start']).utcoffset(),
                             timedelta(0))
            started = time.monotonic()
            with self.assertRaises(state.LauncherBusy) as caught:
                with state.lock(self.c, self.name):
                    self.fail('contended thread lock must time out')
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 14.5)
            self.assertLess(elapsed, 20)
            self.assertEqual(str(caught.exception),
                             f"thread busy: held by pid {holder.pid} (thread reply record) "
                             f"since {metadata['start']}; retry")
            self.assertEqual(state.read(self.c, self.name + '-holder'), metadata)
        self.assertIsNone(state.read(self.c, self.name + '-holder'))
        with state.lock(self.c, self.name, operation='thread check'):
            metadata = state.read(self.c, self.name + '-holder')
            self.assertEqual(metadata['pid'], os.getpid())
            self.assertEqual(metadata['op'], 'thread check')
        self.assertIsNone(state.read(self.c, self.name + '-holder'))

    def test_explicit_zero_remains_nonblocking(self):
        with self.holder(), patch.object(state.time, 'sleep') as sleep:
            with self.assertRaises(state.LauncherBusy):
                with state.lock(self.c, self.name, wait_seconds=0):
                    self.fail('contended nonblocking lock must not be acquired')
            sleep.assert_not_called()

    def test_default_wait_acquires_when_holder_releases_during_poll(self):
        with self.holder() as holder:
            real_sleep = time.sleep

            def release_and_sleep(seconds):
                holder.stdin.close()
                holder.wait()
                real_sleep(seconds)

            with patch.object(state.time, 'sleep', side_effect=release_and_sleep) as sleep:
                with state.lock(self.c, self.name, operation='thread check'):
                    self.assertEqual(holder.returncode, 0)
                    self.assertEqual(state.read(self.c, self.name + '-holder')['pid'],
                                     os.getpid())
                sleep.assert_called_once()
        self.assertIsNone(state.read(self.c, self.name + '-holder'))

    def test_wait_caps_preserve_real_lock_contention(self):
        for name, cap in ((self.name, 15), ('launcher', 15), ('launch-publication', 5)):
            with self.subTest(name=name), self.holder(name), \
                    patch.object(state.time, 'monotonic', side_effect=[0, cap - 0.05, cap]), \
                    patch.object(state.time, 'sleep') as sleep:
                with self.assertRaises(state.LauncherBusy):
                    with state.lock(self.c, name, wait_seconds=60):
                        self.fail('contended lock must respect its wait cap')
                sleep.assert_called_once()
                self.assertAlmostEqual(sleep.call_args.args[0], 0.05)


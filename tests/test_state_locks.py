import contextlib
from datetime import datetime, timedelta
import hashlib
import io
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fleet import cli, launcher, state


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

    def test_slot_main_paths_wait_for_release_and_report_retryable_timeout(self):
        slot = os.path.join(self.temp.name, 'lane')
        name = 'slot-' + hashlib.sha256(os.fsencode(slot)).hexdigest()
        self.c['builder'] = {'slots': [slot]}
        for command, extra in (('launch', []), ('restart', ['--finished-ref', 'TASK-1']),
                               ('relaunch', ['--reason', 'fresh session'])):
            for release in (True, False):
                with self.subTest(command=command, release=release), self.holder(name) as holder:
                    metadata = state.read(self.c, name + '-holder')
                    stderr = io.StringIO()
                    real_sleep = time.sleep

                    def release_and_sleep(seconds):
                        holder.stdin.close()
                        holder.wait()
                        real_sleep(seconds)

                    with patch.object(sys, 'argv', ['fleet', command, 'TASK-1',
                                                   '--slot', slot, *extra]), \
                            patch.object(cli, 'load', return_value=self.c), \
                            patch.object(sys, 'platform', 'linux'), \
                            patch('fleet.steering._main'), \
                            patch.object(launcher, 'resolve_model',
                                         side_effect=ValueError('reached admission')) as admission, \
                            patch.object(state.time, 'monotonic', side_effect=[0, 0, 15]), \
                            patch.object(state.time, 'sleep',
                                         side_effect=release_and_sleep if release else None) as sleep, \
                            contextlib.redirect_stderr(stderr):
                        result = cli.main()
                    sleep.assert_called_once()
                    if release:
                        self.assertEqual(result, 2)
                        admission.assert_called_once()
                        self.assertIn('reached admission', stderr.getvalue())
                    else:
                        self.assertEqual(result, 4)
                        admission.assert_not_called()
                        for detail in (slot, str(holder.pid), metadata['op'], metadata['start']):
                            self.assertIn(detail, stderr.getvalue())
                        self.assertEqual(state.read(self.c, name + '-holder'), metadata)

    def test_slot_default_is_nonblocking_and_explicit_wait_is_capped(self):
        slot = '/scratch/lane'
        with state.slot_lock(self.c, slot, operation='supervisor wake'), \
                patch.object(state.time, 'sleep') as sleep:
            with self.assertRaises(state.LauncherBusy):
                with state.slot_lock(self.c, slot):
                    self.fail('busy supervisor must not acquire lease')
            sleep.assert_not_called()
            with patch.object(state.time, 'monotonic', side_effect=[0, 14.95, 15]):
                with self.assertRaises(state.LauncherBusy):
                    with state.slot_lock(self.c, slot, wait_seconds=60):
                        self.fail('slot wait must be capped')
            sleep.assert_called_once()
            self.assertAlmostEqual(sleep.call_args.args[0], 0.05)


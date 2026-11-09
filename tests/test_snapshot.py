import unittest
from fleet import snapshot


class MainStatusTests(unittest.TestCase):
    def test_coordinator_observation_is_visible(self):
        for state, reason, expected in (
                ('up', 'live omp UI', 'Main: up (live omp UI)'),
                ('down', 'terminal missing', 'Main: DOWN (terminal missing)'),
                ('suspect', 'bare shell prompt', 'Main: suspect (bare shell prompt)'),
                ('unknown', 'screen state unrecognized', 'Main: unknown (screen state unrecognized)')):
            with self.subTest(state=state):
                result = snapshot.render({'repo': 'owner/project', 'errors': [],
                                          'coordinator': {'terminal': 'main', 'state': state,
                                                          'reason': reason}})
                self.assertEqual([line for line in result.splitlines() if line.startswith('Main:')],
                                 [expected])

    def test_unconfigured_coordinator_is_omitted(self):
        for observation in (None, {}, {'terminal': None, 'state': 'unknown'}):
            with self.subTest(observation=observation):
                data = {'repo': 'owner/project', 'errors': []}
                if observation is not None:
                    data['coordinator'] = observation
                self.assertFalse(any(line.startswith('Main:')
                                     for line in snapshot.render(data).splitlines()))


if __name__ == '__main__':
    unittest.main()

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import run


class FakeWebSocket:
    """Minimal stand-in for a `websockets` connection.

    `recv` replays the queued incoming messages and then raises, which is how
    `play()` gets out of its loop. `send` collects what the bot replied.
    """

    def __init__(self, incoming=()):
        self.incoming = [
            m if isinstance(m, str) else json.dumps(m) for m in incoming
        ]
        self.sent = []

    async def recv(self):
        if not self.incoming:
            raise ConnectionResetError('no more messages')
        return self.incoming.pop(0)

    async def send(self, message):
        self.sent.append(json.loads(message))


class HistoryTestCase(unittest.TestCase):
    """Base case for tests touching the module-level HISTORY."""

    def setUp(self):
        run.HISTORY.clear()
        self.addCleanup(run.HISTORY.clear)


class InTempDirTestCase(HistoryTestCase):
    """Base case for tests that write game logs to the working directory."""

    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        previous_cwd = os.getcwd()
        os.chdir(self.tmpdir.name)
        self.addCleanup(os.chdir, previous_cwd)

    def read_log(self, game_id):
        with open(os.path.join(self.tmpdir.name, f"game_{game_id}.log")) as f:
            return f.read()


class TestLogEvent(HistoryTestCase):

    def test_event_is_prefixed_with_an_incoming_marker(self):
        run.log_event('g_1', {'event': 'your_turn'})

        self.assertEqual(run.HISTORY['g_1'], ['< {"event": "your_turn"}'])

    def test_action_is_prefixed_with_an_outgoing_marker(self):
        run.log_action('g_1', {'action': 'move'})

        self.assertEqual(run.HISTORY['g_1'], ['> {"action": "move"}'])

    def test_events_and_actions_keep_the_order_they_happened_in(self):
        run.log_event('g_1', {'event': 'your_turn'})
        run.log_action('g_1', {'action': 'move'})
        run.log_event('g_1', {'event': 'game_over'})

        self.assertEqual(
            run.HISTORY['g_1'],
            [
                '< {"event": "your_turn"}',
                '> {"action": "move"}',
                '< {"event": "game_over"}',
            ],
        )

    def test_each_game_gets_its_own_history(self):
        run.log_event('g_1', {'event': 'your_turn'})
        run.log_event('g_2', {'event': 'your_turn'})

        self.assertEqual(len(run.HISTORY['g_1']), 1)
        self.assertEqual(len(run.HISTORY['g_2']), 1)


class TestWriteGameLog(InTempDirTestCase):

    def test_writes_the_history_one_message_per_line(self):
        run.log_event('g_1', {'event': 'your_turn'})
        run.log_action('g_1', {'action': 'move'})

        run.write_game_log('g_1')

        self.assertEqual(
            self.read_log('g_1'),
            '< {"event": "your_turn"}\n> {"action": "move"}\n',
        )

    def test_writes_only_the_requested_game(self):
        run.log_event('g_1', {'event': 'your_turn'})
        run.log_event('g_2', {'event': 'your_turn'})

        run.write_game_log('g_1')

        self.assertIn('game_g_1.log', os.listdir(self.tmpdir.name))
        self.assertNotIn('game_g_2.log', os.listdir(self.tmpdir.name))

    def test_an_unknown_game_still_produces_a_file(self):
        run.write_game_log('g_unknown')

        self.assertEqual(self.read_log('g_unknown'), '\n')

    def test_a_write_failure_is_reported_and_not_raised(self):
        run.log_event('g_1', {'event': 'game_over'})

        with patch('builtins.open', side_effect=OSError('disk full')):
            run.write_game_log('g_1')  # must not raise: the match is over


class TestSend(unittest.IsolatedAsyncioTestCase):

    async def test_wraps_the_payload_in_an_action_envelope(self):
        websocket = FakeWebSocket()

        await run.send(websocket, 'move', {'col': 3})

        self.assertEqual(
            websocket.sent, [{'action': 'move', 'data': {'col': 3}}]
        )


class TestProcessMove(HistoryTestCase, unittest.IsolatedAsyncioTestCase):

    def your_turn(self, board='|.......|'):
        return {
            'event': 'your_turn',
            'data': {
                'game_id': 'g_1',
                'turn_token': 't_1',
                'side': 'x',
                'board': board,
            },
        }

    async def test_replies_with_a_move_carrying_the_game_and_turn_token(self):
        websocket = FakeWebSocket()

        with patch.object(run, 'randint', return_value=3):
            await run.process_move(websocket, self.your_turn())

        self.assertEqual(
            websocket.sent,
            [
                {
                    'action': 'move',
                    'data': {
                        'game_id': 'g_1',
                        'turn_token': 't_1',
                        'col': 3,
                    },
                }
            ],
        )

    async def test_the_move_is_recorded_in_the_game_history(self):
        websocket = FakeWebSocket()

        with patch.object(run, 'randint', return_value=3):
            await run.process_move(websocket, self.your_turn())

        self.assertEqual(
            run.HISTORY['g_1'],
            ['> {"action": "move", "data": {"game_id": "g_1", '
             '"turn_token": "t_1", "col": 3}}'],
        )

    async def test_the_column_is_picked_from_the_width_of_the_board(self):
        # The board is `|` delimited, so the second `|` marks the end of the
        # first row. Documents the current bounds passed to randint.
        websocket = FakeWebSocket()

        with patch.object(run, 'randint', return_value=0) as randint:
            await run.process_move(websocket, self.your_turn('|....|\n|....|'))

        randint.assert_called_once_with(0, 4)

    async def test_process_your_turn_delegates_to_process_move(self):
        websocket = FakeWebSocket()

        with patch.object(run, 'randint', return_value=1):
            await run.process_your_turn(websocket, self.your_turn())

        self.assertEqual(websocket.sent[0]['action'], 'move')


class TestPlay(InTempDirTestCase, unittest.IsolatedAsyncioTestCase):

    async def test_a_challenge_is_accepted(self):
        websocket = FakeWebSocket([
            {'event': 'challenge', 'data': {'challenge_id': 'c_1'}},
        ])

        await run.play(websocket)

        self.assertEqual(
            websocket.sent,
            [{'action': 'accept_challenge', 'data': {'challenge_id': 'c_1'}}],
        )

    async def test_your_turn_answers_with_a_move(self):
        websocket = FakeWebSocket([
            {
                'event': 'your_turn',
                'data': {
                    'game_id': 'g_1',
                    'turn_token': 't_1',
                    'side': 'x',
                    'board': '|.......|',
                },
            },
        ])

        with patch.object(run, 'randint', return_value=2):
            await run.play(websocket)

        self.assertEqual(websocket.sent[0]['data']['col'], 2)
        # Both the event and the answer end up in the history.
        self.assertEqual(len(run.HISTORY['g_1']), 2)

    async def test_game_over_writes_the_log_for_that_game(self):
        websocket = FakeWebSocket([
            {'event': 'game_over', 'data': {'game_id': 'g_1'}},
        ])

        await run.play(websocket)

        self.assertEqual(
            self.read_log('g_1'),
            '< {"event": "game_over", "data": {"game_id": "g_1"}}\n',
        )

    async def test_game_over_without_a_game_id_writes_nothing(self):
        websocket = FakeWebSocket([{'event': 'game_over', 'data': {}}])

        await run.play(websocket)

        self.assertEqual(os.listdir(self.tmpdir.name), [])

    async def test_events_the_bot_does_not_act_on_are_ignored(self):
        websocket = FakeWebSocket([
            {'event': 'update_user_list', 'data': {'users': []}},
            {'event': 'list_users', 'data': {'users': []}},
        ])

        await run.play(websocket)

        self.assertEqual(websocket.sent, [])
        self.assertEqual(run.HISTORY, {})

    async def test_a_full_match_is_played_and_logged_in_order(self):
        websocket = FakeWebSocket([
            {'event': 'challenge', 'data': {'challenge_id': 'c_1'}},
            {
                'event': 'your_turn',
                'data': {
                    'game_id': 'g_1',
                    'turn_token': 't_1',
                    'side': 'x',
                    'board': '|.......|',
                },
            },
            {'event': 'game_over', 'data': {'game_id': 'g_1'}},
        ])

        with patch.object(run, 'randint', return_value=0):
            await run.play(websocket)

        self.assertEqual(
            [action['action'] for action in websocket.sent],
            ['accept_challenge', 'move'],
        )
        self.assertEqual(
            [line[0] for line in self.read_log('g_1').splitlines()],
            ['<', '>', '<'],
        )

    async def test_a_malformed_message_stops_the_loop_for_a_reconnect(self):
        websocket = FakeWebSocket([
            'not json',
            {'event': 'challenge', 'data': {'challenge_id': 'c_1'}},
        ])

        await run.play(websocket)

        self.assertEqual(websocket.sent, [])


if __name__ == '__main__':
    unittest.main()

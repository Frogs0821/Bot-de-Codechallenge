import unittest
from unittest.mock import patch
import rules


class TestFindSnakes(unittest.TestCase):

    def test_finds_repeated_digits_and_both_crash_food_owners(self):
        rows = [
            "A2Ⓐ#B",
            "a2ⓐbⓑ",
        ]

        (
            own_head, enemy_head, food, obstacles, digits, bonuses,
            enemy_crash_food, own_crash_food,
        ) = rules.find_snakes(rows, "A")

        self.assertEqual(own_head, (0, 0))
        self.assertEqual(enemy_head, (0, 4))
        self.assertEqual(food, [])
        self.assertEqual(digits["2"], [(0, 1), (1, 1)])
        self.assertEqual(bonuses, [])
        self.assertEqual(enemy_crash_food, [(1, 4)])
        self.assertEqual(own_crash_food, [(0, 2), (1, 2)])
        self.assertEqual(obstacles, {(0, 3), (1, 0), (1, 3)})


class TestChooseDirection(unittest.TestCase):

    def setUp(self):
        rules.EXPECTED_DIGIT.clear()
        rules.LAST_DIRECTION.clear()
        rules.LAST_TARGET.clear()
        rules.CRASH_COUNT.clear()
        self.addCleanup(rules.EXPECTED_DIGIT.clear)
        self.addCleanup(rules.LAST_DIRECTION.clear)
        self.addCleanup(rules.LAST_TARGET.clear)
        self.addCleanup(rules.CRASH_COUNT.clear)

    def test_passes_every_copy_of_the_expected_digit_and_crash_food(self):
        turn_data = {
            "game_id": "g_1",
            "side": "A",
            "direction": "left",
            "board": (
                "|.......|\n"
                "|..2.3..|\n"
                "|.......|\n"
                "|.2.Aaa.|\n"
                "|..ⓑ....|\n"
                "|.....B.|\n"
                "|.....bb|"
            ),
            "score_1": 1200,
            "remaining_moves": 300,
            "multiplier_1": 1,
        }

        with patch.object(
            rules, "calculate_direction", return_value=("up", None)
        ) as calculate:
            direction = rules.choose_direction(turn_data)

        self.assertEqual(direction, "up")
        self.assertCountEqual(calculate.call_args.args[3], [(1, 2), (3, 1)])
        self.assertEqual(calculate.call_args.kwargs["crash_food_enemy"], [(4, 2)])
        self.assertEqual(calculate.call_args.kwargs["own_score"], 1200)
        self.assertEqual(calculate.call_args.kwargs["crash_count"], 0)

    def test_choose_direction_runs_with_the_v7_state(self):
        turn_data = {
            "game_id": "g_2",
            "side": "A",
            "direction": "left",
            "board": (
                "|.......|\n"
                "|.......|\n"
                "|.......|\n"
                "|...Aaa.|\n"
                "|.......|\n"
                "|.....B.|\n"
                "|.....bb|"
            ),
            "score_1": 1200,
            "remaining_moves": 300,
            "multiplier_1": 1,
        }

        self.assertIn(rules.choose_direction(turn_data), rules.DIRECTIONS)

    def test_records_a_crash_when_the_selected_move_hits_own_body(self):
        turn_data = {
            "game_id": "g_3",
            "side": "A",
            "direction": "left",
            "board": (
                "|.......|\n"
                "|.......|\n"
                "|.......|\n"
                "|...Aaa.|\n"
                "|.......|\n"
                "|.....B.|\n"
                "|.....bb|"
            ),
            "score_1": 1200,
            "remaining_moves": 300,
            "multiplier_1": 1,
        }

        with patch.object(
            rules, "calculate_direction", return_value=("right", None)
        ):
            self.assertEqual(rules.choose_direction(turn_data), "right")

        self.assertEqual(rules.CRASH_COUNT["g_3"], 1)


if __name__ == "__main__":
    unittest.main()

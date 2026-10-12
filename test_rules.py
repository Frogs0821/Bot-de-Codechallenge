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


class TestLegalMoves(unittest.TestCase):

    def test_calculate_direction_can_move_into_a_vacating_tail(self):
        rows = ["Aa", "aa"]
        head = (0, 0)
        blocked = {(0, 1), (1, 0), (1, 1)}

        with patch("builtins.print") as print_output:
            direction, _ = rules.calculate_direction(
                rows,
                head,
                None,
                [],
                blocked,
                "left",
            )

        self.assertEqual(direction, "down")
        self.assertFalse(
            any(
                call.args[0].startswith("⚠️ Sin salida segura")
                for call in print_output.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()

# lineas del coverage
class TestBonusValue(unittest.TestCase):

    def test_bonus_value_usa_100_movimientos_por_defecto_si_es_none(self):
        # Al pasar None, forzamos a que el código entre en el bloque:
        # if remaining_moves is None: remaining_moves = 100
        valor = rules.bonus_value(None, multiplier=1)
        
        # Como usa 100 / 12 = 8.33, el valor debe ser aprox 2333.33
        # Validamos que devuelva un número mayor a 0
        self.assertGreater(valor, 0)

    def test_bonus_value_baja_si_el_multiplicador_es_alto(self):
        # Probamos el cálculo matemático normal con un multiplicador alto
        valor_mult_1 = rules.bonus_value(50, multiplier=1)
        valor_mult_4 = rules.bonus_value(50, multiplier=4)
        
        # El valor debería ser menor si ya tenemos un buen multiplicador
        self.assertLess(valor_mult_4, valor_mult_1)

    def test_bonus_value_respeta_los_limites_min_y_max(self):
        # Forzamos el límite mínimo (0 turnos restantes, multiplicador gigante)
        valor_min = rules.bonus_value(0, multiplier=100)
        self.assertEqual(valor_min, rules.BONUS_MIN_SCORE)

        # Forzamos el límite máximo (10,000 turnos restantes, multiplicador 1)
        valor_max = rules.bonus_value(10000, multiplier=1)
        self.assertEqual(valor_max, rules.BONUS_MAX_SCORE)

class TestBfsPath(unittest.TestCase):

    def test_bfs_path_mismo_inicio_y_destino(self):
        # Si ya estamos en el destino, devuelve una lista vacía
        rows = ["..."]
        path = rules.bfs_path(rows, (0, 0), (0, 0), set())
        self.assertEqual(path, [])

    def test_bfs_path_encuentra_camino_libre(self):
        # Tablero 1x3: inicio en (0,0) y destino en (0,2)
        rows = ["..."]
        path = rules.bfs_path(rows, (0, 0), (0, 2), set())
        # Debería dar dos pasos hacia la derecha para llegar al objetivo
        self.assertEqual(path, [(0, 1), (0, 2)])

    def test_bfs_path_esquiva_obstaculos(self):
        # Tablero 2x3, bloqueamos la celda (0,1) para que tenga que rodear por abajo
        rows = ["...", "..."]
        blocked = {(0, 1)}
        path = rules.bfs_path(rows, (0, 0), (0, 2), blocked)
        # Camino esperado: Abajo, Derecha, Derecha, Arriba
        self.assertEqual(path, [(1, 0), (1, 1), (1, 2), (0, 2)])

    def test_bfs_path_sin_salida_devuelve_none(self):
        # Encerrado por obstáculos a la derecha (0,1) y abajo (1,0)
        rows = ["...", "..."]
        blocked = {(0, 1), (1, 0)} 
        path = rules.bfs_path(rows, (0, 0), (0, 2), blocked)
        # Como no hay forma de salir, debe devolver None
        self.assertIsNone(path)

class TestCalculateDirection(unittest.TestCase):

    def test_calculate_direction_escenario_completo(self):
        # Simulamos un tablero complejo donde el bot (A) tiene su cuerpo (a), 
        # y el rival (B) tiene su cuerpo (b).
        rows = [
            ".....",
            "..A..",
            "..a..",
            "..B..",
            "..b.."
        ]
        head = (1, 2)
        enemy_head = (3, 2)
        
        # Bloqueamos los cuerpos y cabezas (lo que hace find_snakes)
        blocked = {(1, 2), (2, 2), (3, 2), (4, 2)}
        
        # Le damos un poco de todo para forzar a que entre en TODOS los 'if'
        foods = [(0, 2)]
        bonus_cells = [(1, 0)]          # Las "X"
        crash_food_enemy = [(3, 0)]     # Comida de crash del rival
        own_crash_food = [(1, 4)]       # Nuestra propia comida de crash
        danger_cells = {(0, 0)}         # Dígitos equivocados
        
        # Llamamos al cerebro del bot con toda esta información
        direction, target = rules.calculate_direction(
            rows=rows,
            head=head,
            enemy_head=enemy_head,
            foods=foods,
            blocked=blocked,
            current_direction="up",
            preferred_target=None,
            danger_cells=danger_cells,
            bonus_cells=bonus_cells,
            remaining_moves=100,
            multiplier=3,
            next_food=(0, 4),           # Simula previsión de combo
            crash_food_enemy=crash_food_enemy,
            own_crash_food=own_crash_food,
            own_score=1500,
            crash_count=0
        )
        
        # Verificamos que el bot procesó toda esa info sin crashear 
        # y logró decidir un movimiento válido
        self.assertIn(direction, rules.DIRECTIONS.keys())

    def test_calculate_direction_callejon_sin_salida(self):
        # Simulamos una situación donde el bot está acorralado en una esquina
        rows = ["A#", "##"]
        head = (0, 0)
        blocked = {(0, 1), (1, 0), (1, 1)}
        
        direction, target = rules.calculate_direction(
            rows=rows, 
            head=head, 
            enemy_head=None, 
            foods=[], 
            blocked=blocked, 
            current_direction="left"
        )
        
        # Cuando está encerrado, el bot ejecuta un "fallback". 
        # Verificamos que devuelva la menos mala o None, pero que no rompa el código.
        self.assertTrue(direction is None or direction in rules.DIRECTIONS)

class TestFuncionesAuxiliares(unittest.TestCase):

    def test_enemy_congestion(self):
        # Probamos la función que cuenta enemigos en un radio cercano
        enemy_cells = [(0, 1), (0, 2), (5, 5)]
        # Debería detectar 2 celdas enemigas a distancia <= 3 desde (0,0)
        count = rules.enemy_congestion((0, 0), enemy_cells, radius=3)
        self.assertEqual(count, 2)

    def test_voronoi_score_casos_extremos(self):
        rows = ["....."]
        # Caso 1: Jugar solo (sin enemigo)
        own, enemy = rules.voronoi_score(rows, (0, 0), None, set())
        self.assertEqual(enemy, 0)
        self.assertGreater(own, 0)
        
        # Caso 2: Empate exacto. (0,0) vs (0,4) pelean por el medio (0,2)
        own, enemy = rules.voronoi_score(rows, (0, 0), (0, 4), set())
        # Como (0,2) es un empate exacto de distancia, nadie se lo lleva
        self.assertEqual(own, 2)
        self.assertEqual(enemy, 2)

    def test_get_y_advance_expected_digit(self):
        rules.EXPECTED_DIGIT.clear()
        
        # Si recién empieza la partida y vemos un '3' y un '5', 
        # el bot asume inteligentemente el menor ('3')
        digit = rules.get_expected_digit("game_test", ["3", "5"])
        self.assertEqual(digit, "3")
        
        # Simulamos que come el '9'. El siguiente DEBE ser cíclico: '1'
        rules.advance_expected_digit("game_test", "9")
        self.assertEqual(rules.EXPECTED_DIGIT["game_test"], "1")

    def test_simulate_survival_con_cola_liberandose(self):
        rows = ["...", "..."]
        # Simulamos que estamos rodeados, pero la celda (0,1) 
        # es nuestra cola y se va a liberar en el turno 1.
        free_in = {(0, 1): 1}
        steps, area = rules.simulate_survival(
            rows, 
            start_head=(0, 0), 
            blocked={(0, 1)}, 
            depth=3, 
            free_in=free_in
        )
        # Debe sobrevivir porque la celda se liberó a tiempo
        self.assertGreater(steps, 0)

    def test_trace_own_body_con_cuello(self):
        # Simulamos una víbora enroscada donde es ambiguo para dónde va.
        # Le pasamos el "neck_hint" para que sepa dónde está el cuello.
        rows = ["aaa", "a.a", "aAa"]
        head = (2, 1)
        body_cells = {(0, 0), (0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 2)}
        
        orden = rules.trace_own_body(rows, head, body_cells, neck_hint=(2, 0))
        # Validamos que pudo armar el cuerpo (cabeza + 7 celdas)
        self.assertEqual(len(orden), 8)
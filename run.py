import asyncio
import json
from random import randint
import sys
import websockets
import time
from collections import deque

# A running text log of events received / actions sent per game, written to
# game_<game_id>.log when the match ends.
HISTORY = {}
LAST_DIRECTION = {}
LAST_TARGET = {}

DIRECTIONS = {
    "up": (-1, 0),
    "down": (1, 0),
    "left": (0, -1),
    "right": (0, 1),
}

OPPOSITE = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}

def log_event(game_id, message):
    HISTORY.setdefault(game_id, []).append('< ' + json.dumps(message))


def log_action(game_id, message):
    HISTORY.setdefault(game_id, []).append('> ' + json.dumps(message))


def write_game_log(game_id):
    try:
        with open(f"game_{game_id}.log", "w") as f:
            f.write("\n".join(HISTORY.get(game_id, [])) + "\n")
        print(f"saved game_{game_id}.log")
    except OSError as e:
        print(f"could not write game log: {e}")

def clear_terminal():
    """Limpia la terminal"""
    print("\033[2J\033[H", end="")


def draw_game(board, side, direction=None, score=None, remaining_moves=None):
    """Dibuja el estado actual de Snake."""

    clear_terminal()

    # La otra serpiente
    enemy = 'B' if side == 'A' else 'A'

    print("╔══════════════════════════════════════════╗")
    print("║              🐍 SNAKE BOT               ║")
    print("╠══════════════════════════════════════════╣")

    print(f"║  🟢 TU SERPIENTE: {side:<23}║")
    print(f"║  🔴 RIVAL:         {enemy:<23}║")

    if score is not None:
        print(f"║  🏆 Puntos: {score:<27}║")

    if remaining_moves is not None:
        print(f"║  ⏱️  Movimientos: {remaining_moves:<21}║")

    if direction:
        print(f"║  ➜ Dirección: {direction:<24}║")

    print("╠══════════════════════════════════════════╣")

    # Dibujar tablero
    for row in board.splitlines():

        row = row.strip()

        if not row:
            continue

        visual_row = ""

        for cell in row:

            # Mi cabeza
            if cell == side:
                visual_row += "🟢"

            # Mi cuerpo
            elif cell == side.lower():
                visual_row += "🟩"

            # Cabeza rival
            elif cell == enemy:
                visual_row += "🔴"

            # Cuerpo rival
            elif cell == enemy.lower():
                visual_row += "🟥"

            # Comida
            elif cell == "*":
                visual_row += "🍎"

            # Bordes
            elif cell == "|":
                visual_row += "│"

            # Espacio vacío
            else:
                visual_row += "  "

        print(f"║ {visual_row:<36}║")

    print("╠══════════════════════════════════════════╣")
    print("║  🟢 Cabeza   🟩 Cuerpo                  ║")
    print("║  🔴 Rival    🟥 Cuerpo rival   🍎 Comida ║")
    print("╚══════════════════════════════════════════╝")
    if direction:
        print()
        print(f"  ➜ El bot eligió: {direction}")


async def send(websocket, action, data):
    message = json.dumps(
        {
            'action': action,
            'data': data,
        }
    )
    print(message)
    await websocket.send(message)


async def start(auth_token):
    uri = "wss://server.codechallenge.net.ar/ws?token={}".format(auth_token)
    while True:
        try:
            print('connection to {}'.format(uri))
            async with websockets.connect(uri) as websocket:
                print('connection READY!')
                await play(websocket)
        except KeyboardInterrupt:
            print('Exiting...')
            break
        except Exception as e:
            print(f'connection error! ({type(e).__name__}: {e})')
            time.sleep(3)


async def play(websocket):
    while True:
        try:
            request = await websocket.recv()
            request_data = json.loads(request)
            print(f"< evento: {request_data.get('event')}")
            if request_data['event'] == 'game_over':
                game_id = request_data['data'].get('game_id')
                if game_id:
                    log_event(game_id, request_data)
                    write_game_log(game_id)
            if request_data['event'] == 'challenge':
                # if request_data['data']['opponent'] == 'favoriteopponent':
                await send(
                    websocket,
                    'accept_challenge',
                    {
                        'challenge_id': request_data['data']['challenge_id'],
                    },
                )
            if request_data['event'] == 'your_turn':
                log_event(request_data['data']['game_id'], request_data)
                await process_your_turn(websocket, request_data)
        except KeyboardInterrupt:
            print('Exiting...')
            break
        except (
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.WebSocketException,
        ):
            print('conexión perdida, reconectando...')
            break  # force login again
        except Exception:
            import traceback
            print('error procesando evento (se ignora, seguimos escuchando):')
            traceback.print_exc()
            continue


async def process_your_turn(websocket, request_data):
    # if randint(0, 4) >= 1:
    await process_move(websocket, request_data)

def print_board(board, side, direction=None, score=None, remaining_moves=None):
    print("\033[2J\033[H", end="")

    print("=" * 40)
    print("           🐍 Snake Bot")
    print("=" * 40)

    if score is not None:
        print(f"Puntos: {score}")

    if remaining_moves is not None:
        print(f"Movimientos restantes: {remaining_moves}")

    if direction:
        print(f"Dirección: {direction}")

    print()
    print(board)
    print()

def parse_board(board):
    """Convierte el tablero recibido por el servidor en una matriz."""

    rows = []

    for line in board.splitlines():
        if not line:
            continue

        # Quita solamente los bordes |
        if line.startswith("|"):
            line = line[1:]

        if line.endswith("|"):
            line = line[:-1]

        rows.append(line)

    return rows


def inside_board(rows, r, c):
    return (
        0 <= r < len(rows)
        and 0 <= c < len(rows[r])
    )


def neighbors(rows, position):
    """Devuelve las posiciones vecinas dentro del tablero."""

    r, c = position

    for direction, (dr, dc) in DIRECTIONS.items():
        nr = r + dr
        nc = c + dc

        if inside_board(rows, nr, nc):
            yield direction, (nr, nc)


def find_snakes(rows, side):
    """Encuentra cabeza propia, cabeza rival, comida y obstáculos."""

    enemy = "B" if side == "A" else "A"

    own_head = None
    enemy_head = None
    food = []
    obstacles = set()

    for r, row in enumerate(rows):
        for c, cell in enumerate(row):

            if cell == side:
                own_head = (r, c)

            elif cell == enemy:
                enemy_head = (r, c)

            elif cell == "*":
                food.append((r, c))

            elif cell in "abAB":
                obstacles.add((r, c))

    # La cabeza propia es el punto de partida,
    # así que no debe considerarse un obstáculo.
    if own_head in obstacles:
        obstacles.remove(own_head)

    return own_head, enemy_head, food, obstacles


def bfs_path(rows, start, goal, blocked):
    """
    Busca el camino más corto entre start y goal.

    Devuelve una lista de posiciones.
    Si no existe camino, devuelve None.
    """

    if start == goal:
        return []

    queue = deque([start])
    previous = {start: None}

    while queue:

        current = queue.popleft()

        for _, nxt in neighbors(rows, current):

            if nxt in previous:
                continue

            if nxt in blocked and nxt != goal:
                continue

            previous[nxt] = current

            if nxt == goal:
                path = []
                node = nxt

                while node != start:
                    path.append(node)
                    node = previous[node]

                path.reverse()
                return path

            queue.append(nxt)

    return None


def reachable_area(rows, start, blocked):
    """
    Calcula cuántas casillas puede alcanzar desde una posición.
    Sirve para evitar meternos en zonas cerradas.
    """

    if start in blocked:
        return 0

    queue = deque([start])
    visited = {start}

    while queue:

        current = queue.popleft()

        for _, nxt in neighbors(rows, current):

            if nxt in visited:
                continue

            if nxt in blocked:
                continue

            visited.add(nxt)
            queue.append(nxt)

    return len(visited)


def legal_moves(rows, position, blocked):
    """Devuelve movimientos que no chocan inmediatamente."""

    result = []

    for direction, nxt in neighbors(rows, position):

        if nxt in blocked:
            continue

        result.append((direction, nxt))

    return result


def food_score(
    rows,
    position,
    food,
    blocked,
    enemy_head,
    preferred_target=None,
    total_foods=1
):
    """
    Puntúa una comida teniendo en cuenta:
    - distancia propia
    - distancia del rival
    - si llega antes
    - si es la comida a la que ya veníamos apuntando (para no
      dudar entre dos objetivos parecidos turno a turno)
    - cuánta comida hay en total en el mapa: si hay poca,
      vale la pena viajar lejos; si hay
      mucha, conviene ser selectivo e ir a lo rápido/seguro.
    """

    own_path = bfs_path(
        rows,
        position,
        food,
        blocked
    )

    if own_path is None:
        return None

    own_distance = len(own_path)

    # Para calcular la ruta del rival necesita permitir
    # que empiece desde su propia cabeza.
    enemy_blocked = set(blocked)

    if enemy_head is not None:
        enemy_blocked.discard(enemy_head)
        enemy_path = bfs_path(
            rows,
            enemy_head,
            food,
            enemy_blocked
        )
    else:
        enemy_path = None

    if enemy_path is None:
        enemy_distance = 999
    else:
        enemy_distance = len(enemy_path)

    score = 1000

    # Con poca comida en el mapa 
    # (1-2), conviene ir por la que haya aunque esté lejos
    # no hay de otra. Con comida abundante mejor prioriza
    # lo cercano y deja pasar lo lejano, porque
    # seguramente aparezca algo mejor más cerca pronto.
    if total_foods <= 2:
        distance_weight = 15
    elif total_foods >= 5:
        distance_weight = 40
    else:
        distance_weight = 25

    score -= own_distance * distance_weight

    # Quiere llegar antes que el rival.
    race_difference = enemy_distance - own_distance

    score += race_difference * 35

    # Si el rival llega antes, lo penaliza — pero mucho menos
    # si esta es la única comida disponible.
    if enemy_distance <= own_distance:
        score -= 100 if total_foods <= 2 else 300

    # Si llega claramente antes, premiamos.
    elif enemy_distance >= own_distance + 3:
        score += 250

    # Evita zigzaguear cambiando de objetivo cada turno
    # entre dos comidas de puntaje parecido.
    if preferred_target is not None and food == preferred_target:
        score += 120

    return score


def choose_target(
    rows,
    head,
    foods,
    blocked,
    enemy_head,
    preferred_target=None
):
    """Elige la comida más conveniente."""

    best_food = None
    best_score = float("-inf")

    total_foods = len(foods)

    for food in foods:

        score = food_score(
            rows,
            head,
            food,
            blocked,
            enemy_head,
            preferred_target,
            total_foods
        )

        if score is None:
            continue

        if score > best_score or (
            score == best_score and food == preferred_target
        ):
            best_score = score
            best_food = food

    return best_food


def simulate_survival(rows, start_head, blocked, depth):
    """
    Simula varios movimientos propios hacia adelante para detectar
    si un camino que HOY parece amplio termina cerrándose (un
    "cuello de botella" que recién se nota unos turnos más tarde).

    """

    current_blocked = set(blocked)
    current_head = start_head
    steps = 0

    for _ in range(depth):

        candidates = legal_moves(rows, current_head, current_blocked)

        if not candidates:
            break

        best_next = None
        best_area = -1

        for _, nxt in candidates:

            trial_blocked = current_blocked | {current_head}
            area = reachable_area(rows, nxt, trial_blocked)

            if area > best_area:
                best_area = area
                best_next = nxt

        current_blocked.add(current_head)
        current_head = best_next
        steps += 1

    final_area = reachable_area(rows, current_head, current_blocked)

    return steps, final_area


def choose_direction(
    rows,
    head,
    enemy_head,
    foods,
    blocked,
    current_direction,
    preferred_target=None
):
    """
    Decide el próximo movimiento.
    Combina comida + seguridad + espacio disponible.

    Devuelve (direccion, target_elegido). target_elegido es la
    comida hacia la que apunta esa decisión (o None si no hay
    ninguna alcanzable) — se guarda para pasarla como
    preferred_target la próxima vez y así no dudar entre dos
    objetivos parecidos turno a turno.
    """

    moves = legal_moves(
        rows,
        head,
        blocked
    )

    if not moves:

        # Callejón sin salida real: no hay ningún movimiento
        # que no choque contra algo. Hay que mandar
        # ALGO porque quedarse sin responder cuesta un timeout
        # que es peor que perder jugando.
        fallback_direction = None
        fallback_area = -1

        for direction, (dr, dc) in DIRECTIONS.items():

            new_head = (head[0] + dr, head[1] + dc)

            if not inside_board(rows, *new_head):
                continue

            if current_direction in OPPOSITE and direction == OPPOSITE[current_direction]:
                continue

            area = reachable_area(rows, new_head, set(blocked) | {head})

            if area > fallback_area:
                fallback_area = area
                fallback_direction = direction

        if fallback_direction is not None:
            print("⚠️ Sin salida segura, jugando la menos mala:", fallback_direction)
            return fallback_direction, None

        return None, None

    # Evita invertir inmediatamente la dirección.
    if current_direction in OPPOSITE:

        opposite = OPPOSITE[current_direction]

        non_reverse = [
            move
            for move in moves
            if move[0] != opposite
        ]

        if non_reverse:
            moves = non_reverse

    best_direction = None
    best_target = None
    best_score = float("-inf")

    for direction, new_head in moves:

        # ------------------------------------------------
        # 1. Simula su nueva posición.
        # ------------------------------------------------

        simulated_blocked = set(blocked)

        # Nuestra antigua cabeza pasa a formar parte
        # del cuerpo después de movernos.
        simulated_blocked.add(head)

        # ------------------------------------------------
        # 2. Calcula cuánto espacio tendrá.
        # ------------------------------------------------

        area = reachable_area(
            rows,
            new_head,
            simulated_blocked
        )

        # Mucho espacio = muy bueno.
        score = area * 8

        # Tener varias salidas es bueno.
        mobility = len(
            legal_moves(
                rows,
                new_head,
                simulated_blocked
            )
        )

        score += mobility * 30

        # ------------------------------------------------
        # 2.b Mira varios turnos hacia adelante: ¿este
        #     camino se termina cerrando solo, aunque
        #     ahora mismo parezca amplio?
        # ------------------------------------------------

        LOOKAHEAD_DEPTH = 6

        steps_survived, future_area = simulate_survival(
            rows,
            new_head,
            simulated_blocked,
            LOOKAHEAD_DEPTH
        )

        # Si sobrevive todo el horizonte simulado, no detectamos
        # ninguna trampa cercana: apenas un desempate MENOR según
        # cuánto espacio le queda a futuro.
        if steps_survived >= LOOKAHEAD_DEPTH:
            score += future_area * 0.3

        else:
            # Quedó sin movimientos DENTRO del horizonte simulado:
            # esto es una señal fuerte de encierro. 
            score -= (LOOKAHEAD_DEPTH - steps_survived) * 500

        # ------------------------------------------------
        # 3. Evita acercarse demasiado al rival.
        # ------------------------------------------------

        if enemy_head is not None:

            enemy_distance = (
                abs(new_head[0] - enemy_head[0])
                + abs(new_head[1] - enemy_head[1])
            )

            if enemy_distance == 0:
                score -= 10000

            elif enemy_distance == 1:
                score -= 500

        # ------------------------------------------------
        # 4. Busca la mejor comida desde esta posición.
        # ------------------------------------------------

        target = choose_target(
            rows,
            new_head,
            foods,
            simulated_blocked,
            enemy_head,
            preferred_target
        )

        eats_now = False

        if target is not None:

            path = bfs_path(
                rows,
                new_head,
                target,
                simulated_blocked
            )

            if path is not None:

                distance = len(path)

                # La comida sigue siendo LA prioridad.
                score += 1000

                # Pero no querrá recorrer medio mapa
                # si puede conseguir otra.
                score -= distance * 25

                # Comer la manzana YA (distancia 0) es
                # muchísimo mejor que acercarse
                if distance == 0:
                    eats_now = True
                    score += 600

        # ------------------------------------------------
        # 5. Penalización fuerte por quedar encerrados.
        # ------------------------------------------------

        if area <= 3:
            score -= 1000

        elif area <= 8:
            score -= 400

        # ------------------------------------------------
        # 6. Preferencia parano ir contra los bordes
        # si se puede.
        # ------------------------------------------------

        r, c = new_head
        height = len(rows)
        width = len(rows[0])

        distance_to_wall = min(
            r,
            c,
            height - 1 - r,
            width - 1 - c
        )

        if not eats_now:

            if distance_to_wall == 0:
                score -= 80

            elif distance_to_wall == 1:
                score -= 25

        print(
            f"  {direction:>5} -> "
            f"score={score:7.1f} "
            f"area={area:3} "
            f"salidas={mobility}"
        )

        if score > best_score:
            best_score = score
            best_direction = direction
            best_target = target

    return best_direction, best_target

async def process_move(websocket, request_data):

    data = request_data["data"]

    game_id = data["game_id"]
    turn_token = data["turn_token"]
    board = data["board"]
    side = data["side"]

    rows = parse_board(board)

    (
        head,
        enemy_head,
        foods,
        blocked
    ) = find_snakes(rows, side)

    if head is None:
        print("ERROR: no pude encontrar nuestra cabeza.")
        return

    current_direction = (
        data.get("direction")
        or LAST_DIRECTION.get(game_id)
    )

    draw_game(
        board,
        side,
        direction=current_direction,
        score=data.get("score"),
        remaining_moves=data.get("remaining_moves"),
    )

    direction, target = choose_direction(
        rows,
        head,
        enemy_head,
        foods,
        blocked,
        current_direction,
        preferred_target=LAST_TARGET.get(game_id)
    )

    if direction is None:
        print("⚠️ No encontré un movimiento seguro.")
        return

    print(f"🧠 DECISIÓN: {direction.upper()}")

    move = {
        "game_id": game_id,
        "turn_token": turn_token,
        "direction": direction,
    }

    LAST_DIRECTION[game_id] = direction
    LAST_TARGET[game_id] = target

    log_action(
        game_id,
        {
            "action": "move",
            "data": move
        }
    )

    await send(
        websocket,
        "move",
        move
    )


async def process_wall(websocket, request_data):
    await send(
        websocket,
        'wall',
        {
            'game_id': request_data['data']['game_id'],
            'turn_token': request_data['data']['turn_token'],
            'row': randint(0, 8),
            'col': randint(0, 8),
            'orientation': 'h' if randint(0, 1) == 0 else 'v'
        },
    )


if __name__ == '__main__':
    if len(sys.argv) >= 2:
        auth_token = sys.argv[1]
        asyncio.run(start(auth_token))
    else:
        print('please provide your auth_token')
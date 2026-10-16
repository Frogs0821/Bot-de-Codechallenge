from collections import deque

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

LAST_DIRECTION = {}
LAST_TARGET = {}
EXPECTED_DIGIT = {}
CRASH_COUNT = {}
BONUS_CHARS = ("X", "x")
BONUS_STEPS_PER_FOOD = 12
BONUS_MIN_SCORE = 200
BONUS_MAX_SCORE = 2500


def bonus_value(remaining_moves, multiplier=1):

    if remaining_moves is None:
        remaining_moves = 100

    expected_foods = max(0, remaining_moves) / BONUS_STEPS_PER_FOOD

    value = (250 + 250 * expected_foods) / max(1, multiplier)

    return max(BONUS_MIN_SCORE, min(BONUS_MAX_SCORE, value))


def parse_board(board):

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

    r, c = position

    for direction, (dr, dc) in DIRECTIONS.items():
        nr = r + dr
        nc = c + dc

        if inside_board(rows, nr, nc):
            yield direction, (nr, nc)


def find_snakes(rows, side):
    enemy = "B" if side == "A" else "A"

    own_crash_chars = {"\u24b6", "\u24d0"} if side == "A" else {"\u24b7", "\u24d1"}
    enemy_crash_chars = {"\u24b7", "\u24d1"} if side == "A" else {"\u24b6", "\u24d0"}

    own_head = None
    enemy_head = None
    food = []
    digits = {}
    bonuses = []
    obstacles = set()
    crash_food_enemy = []
    own_crash_food = []
    bombs_big = []
    bombs_small = []

    for r, row in enumerate(rows):
        for c, cell in enumerate(row):

            if cell == side:
                own_head = (r, c)
            elif cell == enemy:
                enemy_head = (r, c)
            elif cell == "*":
                food.append((r, c))
            elif cell.isdigit() and cell != "0":
                digits.setdefault(cell, []).append((r, c))
            elif cell in BONUS_CHARS:
                bonuses.append((r, c))
            elif cell in enemy_crash_chars:
                crash_food_enemy.append((r, c))
            elif cell in own_crash_chars:
                own_crash_food.append((r, c))
            elif cell == "💣":
                bombs_big.append((r, c))
            elif cell == "🧨":
                bombs_small.append((r, c))
            elif cell in "ab#":
                obstacles.add((r, c))

    if own_head in obstacles:
        obstacles.remove(own_head)

    return (
        own_head, enemy_head, food, obstacles, digits, bonuses,
        crash_food_enemy, own_crash_food, bombs_big, bombs_small
    )

def get_expected_digit(game_id, digits_present):

    expected = EXPECTED_DIGIT.get(game_id)

    if expected is None and digits_present:
        expected = min(digits_present, key=int)
        EXPECTED_DIGIT[game_id] = expected

    return expected


def advance_expected_digit(game_id, eaten_digit):
    EXPECTED_DIGIT[game_id] = str((int(eaten_digit) % 9) + 1)


def bfs_path(rows, start, goal, blocked):

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


def trace_own_body(rows, head, body_cells, neck_hint=None, limit=20000):

    if not body_cells:
        return [head]

    path = [head]
    seen = {head}
    best = [head]
    budget = [limit]

    def free_neighbors(cell):
        options = []
        for _, nxt in neighbors(rows, cell):
            if nxt in body_cells and nxt not in seen:
                options.append(nxt)
        return options

    if neck_hint is not None and neck_hint in body_cells:
        path.append(neck_hint)
        seen.add(neck_hint)
        best = list(path)

    def extend():

        if len(path) - 1 == len(body_cells):
            return True

        budget[0] -= 1

        if budget[0] < 0:
            return False

        options = free_neighbors(path[-1])
        options.sort(key=lambda n: len(free_neighbors(n)))

        for n in options:

            path.append(n)
            seen.add(n)

            if len(path) > len(best):
                best[:] = path

            if extend():
                return True

            path.pop()
            seen.discard(n)

        return False

    if extend():
        return path

    remaining = body_cells - set(best)

    return best + sorted(remaining)


def own_body_free_times(body_order):

    body = body_order[1:]  # sin la cabeza
    total = len(body)

    return {
        cell: total - i
        for i, cell in enumerate(body)
    }


def reachable_area_dynamic(rows, start, blocked, free_times, max_time=60):

    hard_blocked = blocked - set(free_times)

    if start in hard_blocked:
        return 0

    visited = {start}
    frontier = [start]
    time = 0

    while frontier and time < max_time:

        time += 1
        next_frontier = []

        for cell in frontier:
            for _, nxt in neighbors(rows, cell):

                if nxt in visited:
                    continue

                if nxt in hard_blocked:
                    continue

                free_at = free_times.get(nxt)

                if free_at is not None and free_at > time:
                    continue

                visited.add(nxt)
                next_frontier.append(nxt)

        frontier = next_frontier

    return len(visited)


def legal_moves(rows, position, blocked, passable_cells=None):
    """Devuelve movimientos que no chocan inmediatamente."""

    result = []

    for direction, nxt in neighbors(rows, position):

        if nxt in blocked and (
            passable_cells is None or nxt not in passable_cells
        ):
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
    total_foods=1,
    next_food=None
):

    own_path = bfs_path(
        rows,
        position,
        food,
        blocked
    )

    if own_path is None:
        return None

    own_distance = len(own_path)
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

    if total_foods <= 2:
        distance_weight = 15
    elif total_foods >= 5:
        distance_weight = 40
    else:
        distance_weight = 25

    score -= own_distance * distance_weight
    if next_food is not None:
        path_to_next = bfs_path(rows, food, next_food, blocked)
        if path_to_next is not None:

            score -= len(path_to_next) * 12


    race_difference = enemy_distance - own_distance

    score += race_difference * 35

    if enemy_distance <= own_distance:
        score -= 100 if total_foods <= 2 else 300

    elif enemy_distance >= own_distance + 3:
        score += 250

    if preferred_target is not None and food == preferred_target:
        score += 120

    return score


def choose_target(
    rows,
    head,
    foods,
    blocked,
    enemy_head,
    preferred_target=None,
    next_food=None
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
            total_foods,
            next_food
        )

        if score is None:
            continue

        if score > best_score or (
            score == best_score and food == preferred_target
        ):
            best_score = score
            best_food = food

    return best_food


def simulate_survival(rows, start_head, blocked, depth, free_in=None):
    current_blocked = set(blocked)
    current_head = start_head
    steps = 0

    for step_number in range(1, depth + 1):

        if free_in:
            for cell, free_at in free_in.items():
                if free_at <= step_number:
                    current_blocked.discard(cell)

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


def enemy_congestion(position, enemy_cells, radius=3):
    count = 0

    for cell in enemy_cells:

        distance = abs(position[0] - cell[0]) + abs(position[1] - cell[1])

        if distance <= radius:
            count += 1

    return count


def voronoi_score(rows, own_head, enemy_head, blocked):

    if enemy_head is None:
        return reachable_area(rows, own_head, blocked), 0

    claimed = {}
    queue = deque()

    if own_head not in blocked:
        queue.append((own_head, 0, "A"))
        claimed[own_head] = (0, "A")

    if enemy_head not in blocked:
        queue.append((enemy_head, 0, "B"))
        if enemy_head == own_head:
            claimed[enemy_head] = (0, "tie")
        else:
            claimed[enemy_head] = (0, "B")

    while queue:

        pos, dist, owner = queue.popleft()

        for _, nxt in neighbors(rows, pos):

            if nxt in blocked:
                continue

            new_dist = dist + 1

            if nxt not in claimed:
                claimed[nxt] = (new_dist, owner)
                queue.append((nxt, new_dist, owner))

            elif claimed[nxt][0] == new_dist and claimed[nxt][1] != owner:
                claimed[nxt] = (new_dist, "tie")

    own_cells = sum(1 for _, owner in claimed.values() if owner == "A")
    enemy_cells = sum(1 for _, owner in claimed.values() if owner == "B")

    return own_cells, enemy_cells


def calculate_direction(
    rows,
    head,
    enemy_head,
    foods,
    blocked,
    current_direction,
    preferred_target=None,
    danger_cells=None,
    bonus_cells=None,
    remaining_moves=None,
    multiplier=1,
    next_food=None,
    crash_food_enemy=None,
    own_crash_food=None,
    own_score=None,
    crash_count=0,
    blast_zones=None
    ):
    if blast_zones is None:
        blast_zones = set()

    if danger_cells is None:
        danger_cells = set()

    if bonus_cells is None:
        bonus_cells = []

    if crash_food_enemy is None:
        crash_food_enemy = []

    if own_crash_food is None:
        own_crash_food = []

    own_letter = rows[head[0]][head[1]]
    own_body_letter = own_letter.lower()
    own_body_cells = {
        (r, c)
        for r, row in enumerate(rows)
        for c, cell in enumerate(row)
        if cell == own_body_letter
    }
    own_body_length = len(own_body_cells) + 1

    neck_hint = None
    if current_direction in DIRECTIONS:
        dr, dc = DIRECTIONS[current_direction]
        neck_hint = (head[0] - dr, head[1] - dc)

    own_body_order = trace_own_body(rows, head, own_body_cells, neck_hint)
    own_free_times = own_body_free_times(own_body_order)

    tail_cells = set()
    if (
        len(own_body_cells) > 1
        and neck_hint in own_body_cells
        and own_body_order[-1] != neck_hint
        and all(
            abs(first[0] - second[0]) + abs(first[1] - second[1]) == 1
            for first, second in zip(own_body_order, own_body_order[1:])
        )
    ):
        tail_cells.add(own_body_order[-1])

    moves = legal_moves(
        rows,
        head,
        blocked,
        passable_cells=tail_cells,
    )

    if not moves:

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

    enemy_cells = []

    if enemy_head is not None:
        enemy_letter = rows[enemy_head[0]][enemy_head[1]]
        enemy_body_letter = enemy_letter.lower()
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                if cell == enemy_body_letter:
                    enemy_cells.append((r, c))
        enemy_cells.append(enemy_head)

    for direction, new_head in moves:

        simulated_blocked = set(blocked)
        simulated_blocked.add(head)
        if new_head in blast_zones:
            score -= 5000
            if own_score is not None and own_score > 0:
                score -= own_score * 3
        simulated_blocked.update(blast_zones)

        area = reachable_area_dynamic(
            rows,
            new_head,
            simulated_blocked,
            own_free_times
        )

        score = area * 8

        own_voronoi, enemy_voronoi = voronoi_score(
            rows,
            new_head,
            enemy_head,
            simulated_blocked
        )

        voronoi_advantage = own_voronoi - enemy_voronoi
        score += voronoi_advantage * 4
        if area < own_body_length:
            faltante = own_body_length - area
            score -= 5000 + 200 * faltante

        if new_head in danger_cells:
            score -= 700

        # Tener varias salidas es bueno.
        mobility = len(
            legal_moves(
                rows,
                new_head,
                simulated_blocked
            )
        )

        score += mobility * 30
        LOOKAHEAD_DEPTH = 6

        steps_survived, future_area = simulate_survival(
            rows,
            new_head,
            simulated_blocked,
            LOOKAHEAD_DEPTH,
            free_in=own_free_times
        )
        if steps_survived >= LOOKAHEAD_DEPTH:
            score += future_area * 0.3

        else:
            # Quedó sin movimientos DENTRO del horizonte simulado:
            # esto es una señal fuerte de encierro. 
            score -= (LOOKAHEAD_DEPTH - steps_survived) * 500

        if enemy_head is not None:

            enemy_distance = (
                abs(new_head[0] - enemy_head[0])
                + abs(new_head[1] - enemy_head[1])
            )

            if enemy_distance == 0:
                score -= 10000
                # Colisión directa.  Antes era mortal, ahora es
                # un crash que duele pero no mata.
                if crash_count == 0 and own_score is not None and own_score > 0:
                    # Primer crash con score positivo: perderíamos
                    # todo el score (wipe a 0).  Muy malo.
                    score -= max(3000, own_score * 2)
                else:
                    # Crashes posteriores: -500 fijo.
                    # Sigue siendo malo pero no catastrófico.
                    score -= 2000

            elif enemy_distance == 1:
                score -= 500
                # Una celda de distancia: riesgo alto de crash
                # el turno siguiente.
                score -= 400

        target = choose_target(
            rows,
            new_head,
            foods,
            simulated_blocked,
            enemy_head,
            preferred_target,
            next_food
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
                score += 1000
                score -= distance * 25
                if distance == 0:
                    eats_now = True
                    score += 600
        if bonus_cells:

            closest_bonus = None

            for bonus in bonus_cells:

                bonus_path = bfs_path(
                    rows,
                    new_head,
                    bonus,
                    simulated_blocked
                )

                if bonus_path is None:
                    continue

                if closest_bonus is None or len(bonus_path) < closest_bonus:
                    closest_bonus = len(bonus_path)

            if closest_bonus is not None:

                value = bonus_value(remaining_moves, multiplier)
                step_weight = value / 100.0

                score -= closest_bonus * step_weight

                if closest_bonus == 0:
                    eats_now = True
                    score += value

        if crash_food_enemy:

            # Valor base de cada unidad de crash food (+100).
            # Con multiplicador alto vale más perseguirla.
            crash_food_value = 100 * multiplier

            # Buscamos la crash food más cercana.
            closest_crash = None

            for cf in crash_food_enemy:

                cf_path = bfs_path(
                    rows,
                    new_head,
                    cf,
                    simulated_blocked
                )

                if cf_path is None:
                    continue

                if closest_crash is None or len(cf_path) < closest_crash:
                    closest_crash = len(cf_path)

            if closest_crash is not None:
                # Peso por paso: similar a la comida normal pero
                # escalado por su valor.  crash_food_value de 100
                # da ~10 por paso; con x3 mult da ~30 por paso.
                cf_step_weight = min(crash_food_value / 10.0, 25)
                score -= closest_crash * cf_step_weight

                # Pisarla ahora: gran premio.
                if closest_crash == 0:
                    eats_now = True
                    score += crash_food_value * 3

        # ------------------------------------------------
        # 4.d v7: nuestra propia comida de crash — denegación.
        #
        # Si hay comida de crash NUESTRA en el tablero, el
        # rival puede comerla para ganar +100 y crecer. Si
        # estamos cerca y no hay mejor opción, conviene pisarla
        # para limpiarla.  Pero es baja prioridad comparada
        # con comer dígitos reales.
        # ------------------------------------------------

        if own_crash_food:

            closest_own_crash = None

            for ocf in own_crash_food:

                ocf_path = bfs_path(
                    rows,
                    new_head,
                    ocf,
                    simulated_blocked
                )

                if ocf_path is None:
                    continue

                if closest_own_crash is None or len(ocf_path) < closest_own_crash:
                    closest_own_crash = len(ocf_path)

            if closest_own_crash is not None:
                # Incentivo bajo: solo vale la pena si estamos
                # cerca y no hay dígitos accesibles.
                score -= closest_own_crash * 3

                # Pisarla ahora: moderadamente bueno (deniega
                # al rival sin coste propio).
                if closest_own_crash == 0:
                    score += 150

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

def choose_direction(turn_data):

    game_id = turn_data.get("game_id")
    board = turn_data.get("board")
    side = turn_data.get("side")

    rows = parse_board(board)
    (
    head, enemy_head, foods, blocked, digits, bonuses,
    crash_food_enemy, own_crash_food, bombs_big, bombs_small
    ) = find_snakes(rows, side)
    blast_zones = set()
    #Bomba grande
    for br, bc in bombs_big:
        for r in range(br - 2, br + 3):
            for c in range(bc - 2, bc + 3):
                blast_zones.add((r, c))

    #Bomba chica
    for br, bc in bombs_small:
        for r in range(br - 1, br + 2):
            for c in range(bc - 1, bc + 2):
                blast_zones.add((r, c))
    
    direction, target = calculate_direction(
        own_score=own_score,
        crash_count=crash_count,
        blast_zones=blast_zones 
    )
    if head is None:
        return "up"

    remaining_moves = turn_data.get("remaining_moves")
    multiplier = turn_data.get("multiplier_1" if side == "A" else "multiplier_2") or 1

    own_score = turn_data.get("score_1" if side == "A" else "score_2")
    crash_count = CRASH_COUNT.get(game_id, 0)

    # Dígito actual a comer
    expected_digit_str = get_expected_digit(game_id, digits.keys())

    target_foods = list(foods) + digits.get(expected_digit_str, [])

    next_food_pos = None
    if expected_digit_str is not None:
        next_digit_int = (int(expected_digit_str) % 9) + 1
        next_digit_str = str(next_digit_int)
        
        if next_digit_str in digits and len(digits[next_digit_str]) > 0:
            next_food_pos = digits[next_digit_str][0]
            if head is not None:
                best_dist = float("inf")
                for pos in digits[next_digit_str]:
                    dist = abs(head[0] - pos[0]) + abs(head[1] - pos[1])
                    if dist < best_dist:
                        best_dist = dist
                        next_food_pos = pos
            else:
                next_food_pos = digits[next_digit_str][0]

    # Casillas de peligro (dígitos incorrectos)
    danger_cells = set()
    for d, positions in digits.items():
        if d != expected_digit_str:
            danger_cells.update(positions)

    current_direction = turn_data.get("direction") or LAST_DIRECTION.get(game_id)

    # Delegar la decisión al motor
    direction, target = calculate_direction(
        rows,
        head,
        enemy_head,
        target_foods,
        blocked,
        current_direction,
        preferred_target=LAST_TARGET.get(game_id),
        danger_cells=danger_cells,
        bonus_cells=bonuses,
        remaining_moves=remaining_moves,
        multiplier=multiplier,
        next_food=next_food_pos,
        crash_food_enemy=crash_food_enemy,
        own_crash_food=own_crash_food,
        own_score=own_score,
        crash_count=crash_count,
    )

    if direction is None:
        direction = "up"

    LAST_DIRECTION[game_id] = direction
    LAST_TARGET[game_id] = target

    dr, dc = DIRECTIONS[direction]
    new_head = (head[0] + dr, head[1] + dc)
    if (
        not inside_board(rows, *new_head)
        or new_head in blocked
        or new_head == enemy_head
    ):
        CRASH_COUNT[game_id] = crash_count + 1

    if expected_digit_str is not None:
        if new_head in digits.get(expected_digit_str, []):
            advance_expected_digit(game_id, expected_digit_str)

    return direction
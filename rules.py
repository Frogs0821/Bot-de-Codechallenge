"""
Reglas de decisión del bot de Snake: parseo del tablero, cálculo de
comida (clásica y dígitos v3), seguridad (área/movilidad/look-ahead)
y elección final de movimiento.

Separado de run.py (que se encarga solo de la conexión websocket,
logging y el dibujo en consola) para poder ajustar la estrategia
sin tocar nada del manejo de la conexión.
"""

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

# v3 (9 sep 2026): la comida ahora son dígitos 1-9 que hay que comer
# en orden ascendente cíclico (1,2,3,...,9,1,2,...). El servidor no
# informa cuál es el próximo dígito correcto, así que lo llevamos
# nosotros: arrancamos asumiendo el dígito más chico visible en el
# tablero (mejor estimación posible sin más info) y lo vamos
# avanzando cada vez que nosotros mismos comemos el correcto.
EXPECTED_DIGIT = {}

# ---------------------------------------------------------------
# v4 (16 sep 2026): el tablero suma dos 'X'.
#
# Comer una X da +50 y sube el multiplicador un escalón (x2, x3,
# ...), de forma PERMANENTE y sin resetearse. El multiplicador
# escala solo los puntos de comida (digito x 100 x multiplicador);
# no escala los +50 de la X, el +1 por movimiento ni las
# penalizaciones de -500. La X no hace crecer la víbora y cada
# jugador tiene su propio multiplicador.
#
# Cuánto vale subir un escalón:
#   cada comida futura rinde `digito x 100` puntos EXTRA por cada
#   escalón. Es decir, el valor de la X depende de cuántas comidas
#   nos queden por comer (más turnos restantes = más valiosa) y
#   NO del multiplicador actual en términos absolutos... pero sí
#   en términos RELATIVOS: con un multiplicador alto, cada comida
#   ya vale mucho, así que conviene priorizar comida antes que ir
#   a buscar otra X. Por eso dividimos por el multiplicador.
# ---------------------------------------------------------------

BONUS_CHARS = ("X", "x")

# Pasos promedio que tarda el bot entre una comida y la siguiente.
# Sirve para estimar cuántas comidas más entran en lo que queda de
# partida. Es una estimación gruesa — ajustable si se ve que el bot
# persigue X de más (subirlo) o de menos (bajarlo).
BONUS_STEPS_PER_FOOD = 12

# Topes, para que la estimación nunca se dispare ni se anule.
BONUS_MIN_SCORE = 200
BONUS_MAX_SCORE = 2500


def bonus_value(remaining_moves, multiplier=1):
    """
    Cuánto vale (en la escala de score interna, donde una comida
    alcanzable suma 1000) ir a comer una X ahora mismo.

    Crece con los turnos que quedan —una X al principio de la
    partida escala muchas comidas; una X a 5 turnos del final casi
    no escala nada— y baja a medida que sube el multiplicador,
    porque con x4 ya encima conviene gastar los turnos comiendo
    dígitos en vez de juntando más X.
    """

    if remaining_moves is None:
        remaining_moves = 100

    expected_foods = max(0, remaining_moves) / BONUS_STEPS_PER_FOOD

    value = (250 + 250 * expected_foods) / max(1, multiplier)

    return max(BONUS_MIN_SCORE, min(BONUS_MAX_SCORE, value))


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
    """
    Encuentra cabeza propia, cabeza rival, comida, obstáculos,
    dígitos y bonus.

    La comida "clásica" (*) se devuelve en `food`. Los dígitos
    ('1'-'9', comida de la v3) se devuelven aparte, en `digits`:
    un diccionario {dígito: [posiciones]}, porque no todo dígito
    en el tablero sirve — solo el que corresponda comer ahora
    según el orden ascendente cíclico.

    Las 'X' (v4) van en `bonuses`: son celdas SEGURAS (se pisan
    sin chocar) que dan +50 y suben el multiplicador un escalón,
    de forma permanente. Ojo: nunca deben terminar en
    `obstacles`, o el bot las esquivaría.
    """

    enemy = "B" if side == "A" else "A"

    own_head = None
    enemy_head = None
    food = []
    digits = {}
    bonuses = []
    obstacles = set()

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

            elif cell in "abAB#":
                obstacles.add((r, c))

    # La cabeza propia es el punto de partida,
    # así que no debe considerarse un obstáculo.
    if own_head in obstacles:
        obstacles.remove(own_head)

    return own_head, enemy_head, food, obstacles, digits, bonuses


def get_expected_digit(game_id, digits_present):
    """
    Determina qué dígito hay que comer ahora.

    Si todavía no sabemos en qué punto del ciclo vamos (arranque
    de partida, o venimos de una reconexión y perdimos el estado
    en memoria), arrancamos asumiendo el dígito más chico visible
    en el tablero — es la mejor estimación posible sin que el
    servidor nos diga el punto de partida real.
    """

    expected = EXPECTED_DIGIT.get(game_id)

    if expected is None and digits_present:
        expected = min(digits_present, key=int)
        EXPECTED_DIGIT[game_id] = expected

    return expected


def advance_expected_digit(game_id, eaten_digit):
    """Avanza al siguiente dígito del ciclo (1..9..1) tras comer el correcto."""
    EXPECTED_DIGIT[game_id] = str((int(eaten_digit) % 9) + 1)


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


def enemy_congestion(position, enemy_cells, radius=3):
    """
    Cuenta cuántas celdas del cuerpo/cabeza rival hay a una
    distancia Manhattan <= radius de `position`.

    Sirve para detectar cuándo el bot se está metiendo (o
    quedando) en una zona apretada junto al rival, ANTES de que
    el área/movilidad lo note — que solo reacciona una vez que
    el espacio ya se redujo. Quedarse dando vueltas pegado a un
    tramo largo de cuerpo rival, aunque el resto del tablero
    esté vacío, es justamente el patrón que llevó a un choque
    real en una partida (mucha comida seguía sin comerse, lejos
    de esa esquina, mientras las dos serpientes se apretujaban
    ahí durante muchos turnos).
    """

    count = 0

    for cell in enemy_cells:

        distance = abs(position[0] - cell[0]) + abs(position[1] - cell[1])

        if distance <= radius:
            count += 1

    return count


def choose_direction(
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
    multiplier=1
):
    """
    Decide el próximo movimiento.
    Combina comida + bonus + seguridad + espacio disponible.

    danger_cells: casillas que se pueden pisar (no bloquean el
    movimiento) pero que conviene evitar si hay alternativa —
    hoy en día, los dígitos que NO corresponde comer (v3): comer
    el equivocado cuesta -500 puntos.

    bonus_cells: las 'X' de la v4. Son seguras de pisar y suben
    el multiplicador de forma permanente. remaining_moves y
    multiplier se usan para saber cuánto vale desviarse a
    buscarlas (ver bonus_value).

    Devuelve (direccion, target_elegido). target_elegido es la
    comida hacia la que apunta esa decisión (o None si no hay
    ninguna alcanzable) — se guarda para pasarla como
    preferred_target la próxima vez y así no dudar entre dos
    objetivos parecidos turno a turno.
    """

    if danger_cells is None:
        danger_cells = set()

    if bonus_cells is None:
        bonus_cells = []

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

    # Celdas del CUERPO rival (sin la cabeza, que ya viene aparte
    # en enemy_head) — para medir congestión más abajo.
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

        # v3: pisar un dígito que NO corresponde comer ahora
        # cuesta -500 puntos reales. Lo evitamos con una
        # penalización aún mayor, para que sea de verdad el
        # último recurso (no algo "casi tan malo como cualquier
        # otra cosa").
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
        # 4.b v4: las X (bonus de multiplicador).
        #
        # Se pisan sin riesgo y el escalón de multiplicador es
        # permanente, así que conviene ir a buscarlas — pero sin
        # abandonar la comida: el valor lo decide bonus_value
        # según los turnos que queden y el multiplicador actual.
        # ------------------------------------------------

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

                # OJO: sumar `value` como constante no serviría de
                # nada. Si la X es alcanzable desde todas las
                # direcciones candidatas, esa constante se suma a
                # todas por igual y se cancela: no cambiaría
                # ninguna decisión. Lo que decide de verdad es
                # cuánto pesa CADA PASO de acercamiento, así que
                # el valor se traduce en ese peso.
                #
                # value 2500 -> 25 por paso (a la par de la comida)
                # value  200 ->  2 por paso (casi indiferente)
                step_weight = value / 100.0

                score -= closest_bonus * step_weight

                # Pisarla en este mismo movimiento sí es un evento
                # puntual de una sola dirección: acá el valor
                # completo sí corresponde.
                if closest_bonus == 0:
                    eats_now = True
                    score += value

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
"""
Reglas de decisión del bot de Snake: parseo del tablero, cálculo de
comida (clásica y dígitos v3), seguridad (área/movilidad/look-ahead)
y elección final de movimiento.

v6 (30 sep 2026): cada dígito del tablero aparece en 3-5 copias.
    Comer cualquiera de ellas elimina TODAS las copias de ese dígito
    y avanza al siguiente.  El bot elige la copia más conveniente.

v7 (7 oct 2026): chocar (cuerpo propio, borde, #, oponente) ya no
    termina la partida.  La serpiente se queda, conserva 3 celdas, y
    el resto del cuerpo se convierte en comida de crash que solo el
    oponente puede comer (+100 × multiplicador).  El primer crash
    pone el score a 0; los siguientes restan -500.  Las partidas
    duran 400 movimientos.  Comida de crash propia se limpia sin
    puntos; la del rival alimenta y da crecimiento.

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

# v7 (7 oct 2026): cuántas veces crasheó cada jugador en la partida.
# Clave = game_id, valor = número de crashes propios registrados.
# Se usa para decidir la penalización: el primer crash pone el score
# a 0 (wiped), los siguientes restan -500.
CRASH_COUNT = {}

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
    dígitos, bonus y comida de crash (v7).

    La comida "clásica" (*) se devuelve en `food`. Los dígitos
    ('1'-'9', comida de la v3) se devuelven aparte, en `digits`:
    un diccionario {dígito: [posiciones]}, porque no todo dígito
    en el tablero sirve — solo el que corresponda comer ahora
    según el orden ascendente cíclico.

    Las 'X' (v4) van en `bonuses`: son celdas SEGURAS (se pisan
    sin chocar) que dan +50 y suben el multiplicador un escalón,
    de forma permanente. Ojo: nunca deben terminar en
    `obstacles`, o el bot las esquivaría.

    v7: comida de crash (Ⓐ / Ⓑ, Unicode U+24B6 / U+24B7).
    - La del RIVAL es comida que podemos comer: +100 y crecemos.
      Va en `crash_food_enemy`.
    - La PROPIA la podemos pisar para limpiarla (denegarla al
      rival): no da puntos ni crecimiento. No va como obstáculo.
    """

    enemy = "B" if side == "A" else "A"

    # Comida de crash: Ⓐ (U+24B6) y Ⓑ (U+24B7).
    # El servidor podría usar variantes minúsculas ⓐ (U+24D0) / ⓑ (U+24D1).
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
                # Comida de crash del rival: la podemos comer (+100)
                crash_food_enemy.append((r, c))

            elif cell in own_crash_chars:
                # Nuestra comida de crash: la podemos pisar para
                # limpiarla (denegar al rival), sin obtener puntos.
                # NO es obstáculo ni comida — solo pisable.
                own_crash_food.append((r, c))

            elif cell in "ab#":
                obstacles.add((r, c))

            # Celdas con letras del rival (cabeza + cuerpo) que no
            # son crash food: son obstáculo.  Pero la cabeza propia
            # y su cuerpo solo se detectan arriba por su letra exacta.

    # La cabeza propia es el punto de partida,
    # así que no debe considerarse un obstáculo.
    if own_head in obstacles:
        obstacles.remove(own_head)

    return (
        own_head, enemy_head, food, obstacles, digits, bonuses,
        crash_food_enemy, own_crash_food
    )


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


def trace_own_body(rows, head, body_cells, neck_hint=None, limit=20000):
    """
    Reconstruye el orden real del propio cuerpo, de la cabeza a la
    cola.

    El tablero no dice en qué orden van los segmentos (todos se ven
    igual, como `a`), así que lo deducimos: como el cuerpo de una
    víbora es un camino simple (sin bifurcaciones ni cruces
    consigo mismo), caminamos desde la cabeza pasando por cada
    celda del cuerpo una sola vez. Casi siempre hay un único camino
    posible; cuando hay más de una opción, probamos primero la
    celda que le queda con MENOS salidas libres — así evitamos
    quedarnos sin por dónde seguir más adelante y tener que volver
    atrás.

    Ojo con un caso ambiguo: si la víbora está enroscada de forma
    que la cabeza queda pegada a DOS celdas de su propio cuerpo a
    la vez (la cola real Y el cuello, el segmento inmediatamente
    anterior a la cabeza), hay dos caminos igual de válidos
    geométricamente y no hay forma de saber cuál es cuál mirando
    solo el tablero de este turno — hace falta memoria del
    movimiento anterior. `neck_hint` es justamente eso: si sabemos
    con certeza qué celda es el cuello (porque ahí estaba nuestra
    propia cabeza el turno pasado), se la pasamos para arrancar el
    camino por el lado correcto sin tener que adivinar. El error,
    si no se resuelve, es conservador: como mucho subestima cuánto
    se libera la cola, nunca al revés.

    Si el cuerpo está tan enroscado que no se completa el camino
    dentro del presupuesto de pasos, devolvemos el mejor camino
    parcial encontrado y agregamos el resto de las celdas al final,
    en cualquier orden. No rompe nada: esas celdas, al no tener un
    orden confiable, terminan tratándose como si nunca se liberaran
    (la misma suposición conservadora que ya usábamos antes de
    esta mejora).
    """

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

    # Si tenemos el dato del cuello, forzamos el primer paso ahí
    # cuando sea una opción válida — resuelve la ambigüedad de raíz
    # en vez de dejarla en manos de la heurística.
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
    """
    A partir del orden cabeza->cola (el que devuelve trace_own_body),
    calcula en cuántos de NUESTROS PROPIOS movimientos se libera
    cada celda del cuerpo.

    La cola se libera en el próximo movimiento (1), la celda antes
    de la cola en 2, y así hasta la celda pegada a la cabeza. Esto
    asume que no comemos nada mientras tanto — si comemos, la cola
    NO se mueve ese turno, así que es una estimación optimista, no
    una garantía exacta. Por eso este cálculo sirve para elegir
    ENTRE opciones, no para asumir a ciegas que un camino angosto
    siempre va a estar libre a tiempo.
    """

    body = body_order[1:]  # sin la cabeza
    total = len(body)

    return {
        cell: total - i
        for i, cell in enumerate(body)
    }


def reachable_area_dynamic(rows, start, blocked, free_times, max_time=60):
    """
    Como reachable_area, pero las celdas del PROPIO cuerpo que
    aparecen en `free_times` se consideran alcanzables una vez que
    el número de pasos para llegar ahí alcanza el turno en que se
    liberan — en vez de bloqueadas para siempre.

    Todo lo demás en `blocked` (cuerpo rival, muros, dígitos a
    evitar) se sigue tratando como obstáculo permanente: no
    tenemos manera confiable de predecir cuándo se libera lo que
    no controlamos nosotros.
    """

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
    total_foods=1,
    next_food=None
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
    # --- PREVISIÓN DE COMBO ---
    if next_food is not None:
        # Se simula cuánto costará ir desde el dígito actual hasta el SIGUIENTE
        path_to_next = bfs_path(rows, food, next_food, blocked)
        if path_to_next is not None:
            # Penaliza el trayecto FUTURO. Ponderación (12) para no opacar
            # la distancia actual, pero forzará a elegir el mejor ángulo de ataque.
            score -= len(path_to_next) * 12

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
    """
    Simula varios movimientos propios hacia adelante para detectar
    si un camino que HOY parece amplio termina cerrándose (un
    "cuello de botella" que recién se nota unos turnos más tarde).

    free_in (opcional): {celda: en cuántos movimientos propios se
    libera}, de own_body_free_times(). Sin esto, el cuerpo propio se
    trata como bloqueado para siempre durante toda la simulación (lo
    conservador de siempre). Con esto, la cola y lo que va detrás
    se van habilitando a medida que avanza la simulación — igual
    que pasaría en la partida real.
    """

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


def voronoi_score(rows, own_head, enemy_head, blocked):
    """
    Estima el control del tablero usando la idea del diagrama de Voronoi:
    hace un BFS simultáneo desde ambas cabezas y clasifica cada celda libre
    según quién llega primero.

    Devuelve (celdas_propias, celdas_rival): cuántas celdas "gana" cada uno.
    La diferencia (celdas_propias - celdas_rival) da una medida de control:
    positivo = ventaja propia, negativo = ventaja del rival.

    Por qué esto importa más que solo mirar el área propia:
    podés tener 100 celdas alcanzables, pero si el rival controla las 80
    donde está la comida, esa área no te sirve de mucho. El Voronoi lo detecta
    porque pondera QUIÉN llega antes a cada zona, no solo si vos podés llegar.

    Si no hay rival (enemy_head es None), todas las celdas alcanzables son
    "propias" — la función sigue siendo útil como medida de área pura.

    Nota sobre tableros con cola que se libera: usamos blocked tal cual se
    recibe (el blocked estático del turno), igual que reachable_area. No
    modelamos la cola que se va liberando porque hacerlo para DOS jugadores
    a la vez introduce suposiciones sobre los movimientos del rival que no
    tenemos manera de verificar. Es conservador pero consistente.
    """

    if enemy_head is None:
        return reachable_area(rows, own_head, blocked), 0

    # BFS simultáneo desde ambas cabezas.
    # Cada celda registra (distancia, dueño): "A" si llegamos primero
    # nosotros, "B" si llega primero el rival, "tie" si empatan.
    claimed = {}
    queue = deque()

    if own_head not in blocked:
        queue.append((own_head, 0, "A"))
        claimed[own_head] = (0, "A")

    if enemy_head not in blocked:
        queue.append((enemy_head, 0, "B"))
        # Si las dos cabezas están en la misma celda (no puede pasar
        # en una partida real, pero lo manejamos por robustez):
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
                # Mismo paso: empate — ninguno se lleva la celda.
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

    crash_food_enemy (v7): comida de crash del RIVAL en el
    tablero. Son celdas seguras que podemos comer para +100 y
    crecimiento.  Se tratan como comida adicional con un
    valor fijo.

    own_crash_food (v7): nuestra propia comida de crash en el
    tablero.  La podemos pisar para limpiarla (denegar al rival)
    sin obtener puntos ni crecer.  No es obstáculo.

    own_score / crash_count (v7): usados para evaluar el riesgo
    de crashear. Con score alto y sin crashes previos, un crash
    es devastador (wipe a 0). Con score bajo o muchos crashes,
    es menos dramático.

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

    if crash_food_enemy is None:
        crash_food_enemy = []

    if own_crash_food is None:
        own_crash_food = []

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

    # Nuestro propio largo (cabeza + cuerpo) y las celdas exactas
    # que ocupa. Sirve para detectar "encierros" que el área por sí
    # sola no deja ver: un espacio de, digamos, 5 celdas puede
    # parecer "espacio de sobra", pero si nuestra víbora ya mide
    # 12, en algún momento no vamos a entrar ahí sin chocarnos
    # contra nosotros mismos.
    own_letter = rows[head[0]][head[1]]
    own_body_letter = own_letter.lower()
    own_body_cells = set()

    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if cell == own_body_letter:
                own_body_cells.add((r, c))

    own_body_length = len(own_body_cells) + 1  # la cabeza cuenta

    # Orden real del cuerpo (de la cabeza a la cola) y en qué
    # movimiento propio se libera cada celda. Si la víbora está tan
    # enroscada que no se pudo reconstruir el camino completo,
    # trace_own_body ya devuelve su mejor aproximación (nunca peor
    # que tratar todo como bloqueado para siempre, que era el
    # comportamiento de antes de esta mejora).
    # Si sabemos hacia dónde nos movimos el turno pasado, podemos
    # calcular exactamente dónde estaba la cabeza antes — esa
    # celda es, sin ninguna duda, el cuello (el segmento pegado a
    # la cabeza actual). Se la damos a trace_own_body para que no
    # tenga que adivinar en el caso ambiguo (víbora enroscada con
    # la cabeza pegada a dos celdas de su propio cuerpo a la vez).
    neck_hint = None

    if current_direction in DIRECTIONS:
        dr, dc = DIRECTIONS[current_direction]
        neck_hint = (head[0] - dr, head[1] - dc)

    own_body_order = trace_own_body(rows, head, own_body_cells, neck_hint)
    own_free_times = own_body_free_times(own_body_order)

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

        area = reachable_area_dynamic(
            rows,
            new_head,
            simulated_blocked,
            own_free_times
        )

        # Mucho espacio = muy bueno.
        score = area * 8

        # Control del tablero (Voronoi): BFS simultáneo desde nuestra
        # nueva posición y la cabeza rival. Cuantas más celdas libres
        # llegamos antes que el rival, mejor — porque eso predice quién
        # va a tener acceso a la comida futura y a las zonas abiertas.
        #
        # El área propia ya captura "cuánto espacio tengo", pero no "cuánto
        # de ese espacio me quedo yo vs el rival". El Voronoi agrega esa
        # dimensión: podés tener 100 celdas alcanzables, pero si el rival
        # controla las 80 donde está la comida, esa ventaja no existe.
        #
        # Peso moderado (4): suficiente para mover la aguja entre opciones
        # casi iguales, sin tapar señales más importantes como la comida
        # (+1000) o la detección de encierro (-5000+).
        own_voronoi, enemy_voronoi = voronoi_score(
            rows,
            new_head,
            enemy_head,
            simulated_blocked
        )

        voronoi_advantage = own_voronoi - enemy_voronoi
        score += voronoi_advantage * 4

        # Detección de encierro por largo propio: si el área
        # alcanzable es menor que nuestra propia víbora, no importa
        # que "parezca" espacio suficiente — no vamos a caber ahí
        # sin chocarnos contra nuestro propio cuerpo eventualmente.
        # Cuanto más grande la diferencia, peor (y esto es una
        # señal MÁS fuerte y MÁS temprana que el look-ahead de más
        # abajo, que recién detecta el problema al simularlo).
        if area < own_body_length:
            faltante = own_body_length - area
            score -= 5000 + 200 * faltante

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
            LOOKAHEAD_DEPTH,
            free_in=own_free_times
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
        #
        # v7: chocar ya no termina la partida, pero sigue
        # costando caro: el primer crash pone el score a 0;
        # los siguientes restan -500.  Además perdemos cuerpo
        # (todo excepto 3 celdas).  La penalización refleja
        # cuánto duele según el estado actual.
        # ------------------------------------------------

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

        # ------------------------------------------------
        # 4. Busca la mejor comida desde esta posición.
        # ------------------------------------------------

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
        # 4.c v7: comida de crash del rival.
        #
        # Cada celda de crash food del rival vale +100 (×mult)
        # al comerla, y además hace crecer. Es comida gratuita
        # que no requiere seguir ningún orden.  Se puntúa como
        # comida adicional con valor fijo.
        # ------------------------------------------------

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
    """
    Función puente: Recibe el JSON del turno (turn_data) desde run.py,
    prepara las variables, calcula el siguiente dígito para armar combos,
    y delega la decisión matemática a calculate_direction.

    v6: cada dígito puede tener 3-5 copias. El bot elige la mejor.
        Al comer cualquier copia, avanza el dígito y todas las
        copias desaparecen.  El combo lookahead (next_food) busca
        la copia más cercana del siguiente dígito.

    v7: comida de crash (Ⓐ/Ⓑ) se extrae del tablero y se pasa
        a calculate_direction. Se trackea crash_count para decidir
        la penalización correcta.
    """
    game_id = turn_data.get("game_id")
    board = turn_data.get("board")
    side = turn_data.get("side")

    rows = parse_board(board)
    (
    head, enemy_head, foods, blocked, digits, bonuses,
    crash_food_enemy, own_crash_food
    ) = find_snakes(rows, side)

    if head is None:
        return "up"  # Fallback seguro por si no nos encontramos en el tablero

    remaining_moves = turn_data.get("remaining_moves")
    multiplier = turn_data.get("multiplier_1" if side == "A" else "multiplier_2") or 1

    # v7: score propio y crash count
    own_score = turn_data.get("score_1" if side == "A" else "score_2")
    crash_count = CRASH_COUNT.get(game_id, 0)

    # Dígito actual a comer
    expected_digit_str = get_expected_digit(game_id, digits.keys())

    # v6: todas las copias del dígito esperado son comida válida.
    # El bot elegirá la mejor vía choose_target → food_score.
    target_foods = list(foods) + digits.get(expected_digit_str, [])

    # --- LÓGICA DE COMBO: Calcular cuál es el SIGUIENTE dígito ---
    # --- LÓGICA DE COMBO (v6-aware): Calcular cuál es el SIGUIENTE dígito ---
    # Con v6, el siguiente dígito también puede tener varias copias.
    # Elegimos la más cercana a nuestra cabeza como referencia para el combo.
    next_food_pos = None
    if expected_digit_str is not None:
        next_digit_int = (int(expected_digit_str) % 9) + 1
        next_digit_str = str(next_digit_int)
        
        # Si el siguiente dígito ya está en el tablero, extraemos su coordenada

        # v6: buscar la copia más cercana del siguiente dígito
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

    # Si el bot decidió un movimiento que come el dígito, avanzamos el registro
    if expected_digit_str is not None:
        if new_head in digits.get(expected_digit_str, []):
            advance_expected_digit(game_id, expected_digit_str)

    return direction
import asyncio
import json
from random import randint
import sys
import websockets
import time


# A running text log of events received / actions sent per game, written to
# game_<game_id>.log when the match ends.
HISTORY = {}


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
    """Dibuja el estado actual de Snake en la terminal"""

    clear_terminal()

    print("╔══════════════════════════════════════╗")
    print("║              🐍 SNAKE BOT            ║")
    print("╠══════════════════════════════════════╣")

    if score is not None:
        print(f"║  Puntos: {score:<28}║")

    if remaining_moves is not None:
        print(f"║  Movimientos restantes: {remaining_moves:<14}║")

    if direction:
        print(f"║  Dirección: {direction:<25}║")

    print("╠══════════════════════════════════════╣")

    # El tablero se dibuja con emojis para representar las serpientes y la comida
    for row in board.splitlines():
        row = row.strip()

        if not row:
            continue

        visual_row = ""

        for cell in row:
            if cell == "A":
                visual_row += "🟢"
            elif cell == "a":
                visual_row += "🟩"
            elif cell == "B":
                visual_row += "🔴"
            elif cell == "b":
                visual_row += "🟥"
            elif cell == "*":
                visual_row += "🍎"
            elif cell == "|":
                visual_row += "│"
            else:
                visual_row += "  "

        print(f"║ {visual_row:<34}║")

    print("╠══════════════════════════════════════╣")
    print("║  🟢 Mi serpiente   🔴 Rival          ║")
    print("║  🍎 Comida                            ║")
    print("╚══════════════════════════════════════╝")

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
    # uri = "ws://localhost:5000/ws?token={}".format(auth_token)
    while True:
        try:
            print('connection to {}'.format(uri))
            async with websockets.connect(uri) as websocket:
                print('connection READY!')
                await play(websocket)
        except KeyboardInterrupt:
            print('Exiting...')
            break
        except Exception:
            print('connection error!')
            time.sleep(3)


async def play(websocket):
    while True:
        try:
            request = await websocket.recv()
            print(f"< {request}")
            request_data = json.loads(request)
            if request_data['event'] == 'update_user_list':
                pass
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
        except Exception as e:
            print('error {}'.format(str(e)))
            break  # force login again


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

async def process_move(websocket, request_data):
    data = request_data['data']

    game_id = data['game_id']
    turn_token = data['turn_token']
    board = data['board']
    side = data['side']

    print_board(
    board,
    side,
    data.get('direction'),
    data.get('score_1') if side == 'A' else data.get('score_2'),
    data.get('remaining_moves')
    )
    # Tablero a matriz
    rows = [
        row.strip('|')
        for row in board.splitlines()
        if row.strip('|')
    ]

    height = len(rows)
    width = len(rows[0])

    # Busca la cabeza, la comida y los obstáculos
    head = None
    food = []

    obstacles = set()

    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if cell == side:
                head = (r, c)
            elif cell == '*':
                food.append((r, c))
            elif cell != ' ':
                # Cualquier parte de una serpiente es un obstáculo
                obstacles.add((r, c))

    if head is None:
        print("No encontré la cabeza de la serpiente.")
        return

    print(f"Snake {side}: cabeza={head}")
    print(f"Comida: {food}")

    # Elegir la comida más cercana
    target = min(
        food,
        key=lambda pos: abs(pos[0] - head[0]) + abs(pos[1] - head[1])
    ) if food else None

    # Posibles movimientos
    directions = {
        'up': (-1, 0),
        'down': (1, 0),
        'left': (0, -1),
        'right': (0, 1),
    }

    # Movimientos seguros
    safe_moves = []

    for direction, (dr, dc) in directions.items():
        nr = head[0] + dr
        nc = head[1] + dc

        # No salir del tablero
        if nr < 0 or nr >= height or nc < 0 or nc >= width:
            continue

        # No chocar contra otra parte de una serpiente
        if (nr, nc) in obstacles:
            continue

        safe_moves.append((direction, nr, nc))

    if not safe_moves:
        print("¡No hay movimientos seguros!")
        return

    # Si hay comida, eligo el movimiento cercano
    if target:
        direction, _, _ = min(
            safe_moves,
            key=lambda move:
                abs(move[1] - target[0]) +
                abs(move[2] - target[1])
        )
    else:
        # Si no eencuentran comida, uso el primer movimiento seguro
        direction = safe_moves[0][0]

    move = {
        'game_id': game_id,
        'turn_token': turn_token,
        'direction': direction,
    }

    print(f"Movimiento elegido: {direction}")

    log_action(
        game_id,
        {
            'action': 'move',
            'data': move
        }
    )

    await send(websocket, 'move', move)


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

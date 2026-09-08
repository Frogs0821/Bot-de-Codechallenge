import asyncio
import json
from random import randint
import sys
import websockets
import time

import rules

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


def draw_game(board, side, direction=None, score=None, remaining_moves=None, expected_digit=None):
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

    if expected_digit is not None:
        print(f"║  🎯 Próximo dígito: {expected_digit:<19}║")

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

            # Comida clásica
            elif cell == "*":
                visual_row += "🍎"

            # v3: dígitos — el correcto es el objetivo, el resto
            # hay que evitarlos (cuestan -500 si se comen mal).
            elif cell.isdigit():
                if cell == expected_digit:
                    visual_row += "🎯"
                else:
                    visual_row += "⚠️"

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
    print("║  🎯 Dígito correcto   ⚠️  Dígito a evitar ║")
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

async def process_move(websocket, request_data):

    data = request_data["data"]

    game_id = data["game_id"]
    turn_token = data["turn_token"]
    board = data["board"]
    side = data["side"]

    rows = rules.parse_board(board)

    (
        head,
        enemy_head,
        foods,
        blocked,
        digits
    ) = rules.find_snakes(rows, side)

    if head is None:
        print("ERROR: no pude encontrar nuestra cabeza.")
        return

    # v3: de todos los dígitos en el tablero, sólo uno es "comida"
    # de verdad ahora mismo (el que sigue en el orden ascendente
    # cíclico); el resto son casillas a evitar (-500 si las pisamos).
    expected_digit = rules.get_expected_digit(game_id, digits.keys())

    target_foods = list(foods) + digits.get(expected_digit, [])

    danger_cells = set()
    for d, positions in digits.items():
        if d != expected_digit:
            danger_cells.update(positions)

    current_direction = (
        data.get("direction")
        or rules.LAST_DIRECTION.get(game_id)
    )

    draw_game(
        board,
        side,
        direction=current_direction,
        score=data.get("score"),
        remaining_moves=data.get("remaining_moves"),
        expected_digit=expected_digit,
    )

    direction, target = rules.choose_direction(
        rows,
        head,
        enemy_head,
        target_foods,
        blocked,
        current_direction,
        preferred_target=rules.LAST_TARGET.get(game_id),
        danger_cells=danger_cells
    )

    if direction is None:
        print("⚠️ No encontré un movimiento seguro.")
        return

    print(f"🧠 DECISIÓN: {direction.upper()}")

    # Si con este movimiento vamos a comer justo el dígito
    # esperado, avanzamos nuestro contador al siguiente del ciclo.
    if expected_digit is not None:
        dr, dc = rules.DIRECTIONS[direction]
        new_head = (head[0] + dr, head[1] + dc)
        if new_head in digits.get(expected_digit, []):
            rules.advance_expected_digit(game_id, expected_digit)

    move = {
        "game_id": game_id,
        "turn_token": turn_token,
        "direction": direction,
    }

    rules.LAST_DIRECTION[game_id] = direction
    rules.LAST_TARGET[game_id] = target

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
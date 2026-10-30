import asyncio
import json
import sys
import time

import websockets

import rules

# En consolas que no soportan emojis (por ejemplo cp1252) el print no debe romper el bot.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

SERVER_URI = "wss://server.codechallenge.net.ar/ws?token={}"

# Dirección que se manda si algo falla antes de decidir: es mejor una jugada
# (aunque sea mala) que dejar que venza el timeout (-500).
FALLBACK_DIRECTION = "up"

# Limpiar la consola con os.system('cls'/'clear') es lento en Windows.
# Lo dejamos apagado por defecto.
CLEAR_SCREEN = False

# A running text log of events received / actions sent per game, written to
# game_<game_id>.log when the match ends.
HISTORY = {}


# ---------------------------------------------------------------------------
# Logs de partida
# ---------------------------------------------------------------------------

def log_event(game_id, message):
    HISTORY.setdefault(game_id, []).append('< ' + json.dumps(message))


def log_action(game_id, message):
    HISTORY.setdefault(game_id, []).append('> ' + json.dumps(message))


def write_game_log(game_id):
    try:
        with open("game_{}.log".format(game_id), "w", encoding="utf-8") as f:
            f.write("\n".join(HISTORY.get(game_id, [])) + "\n")
        print("saved game_{}.log".format(game_id))
    except OSError as e:
        print("could not write game log: {}".format(e))


# ---------------------------------------------------------------------------
# Visualización (solo para mirar; nunca debe afectar la jugada)
# ---------------------------------------------------------------------------

def clear_terminal():
    """Limpia la terminal según el sistema operativo."""
    import os
    os.system('cls' if os.name == 'nt' else 'clear')


SYMBOLS = {
    '*': '🍎',
    'X': '✖️',
    'x': '✖️',
    '#': '🧱',
    '💣': '💣',
    '🧨': '🧨',
    '⭐': '⭐',
    'Ⓐ': 'Ⓐ',
    'Ⓑ': 'Ⓑ',
}


def render_row(row, side, enemy, expected_digit):
    out = []
    for cell in row:
        if cell == side:
            out.append('🟢')
        elif cell == side.lower():
            out.append('🟩')
        elif cell == enemy:
            out.append('🔴')
        elif cell == enemy.lower():
            out.append('🟥')
        elif cell.isdigit():
            if expected_digit is not None and cell == str(expected_digit):
                out.append('🎯')
            else:
                out.append('⚠️' + cell)
        elif cell == '|':
            out.append('│')
        elif cell == ' ':
            out.append('  ')
        else:
            out.append(SYMBOLS.get(cell, cell))
    return ''.join(out)


def dibujar_tablero(board, side, score=None, remaining_moves=None,
                    direction=None, expected_digit=None, multiplier=None):
    """Imprime el estado actual. Es solo visual: cualquier error se ignora."""
    try:
        if CLEAR_SCREEN:
            clear_terminal()

        enemy = 'B' if side == 'A' else 'A'

        print('=' * 44)
        print('SNAKE BOT  |  tú: {}  |  rival: {}'.format(side, enemy))
        print('puntos: {}  |  movimientos: {}  |  x{}  |  próximo dígito: {}'.format(
            score, remaining_moves, multiplier, expected_digit))
        if direction:
            print('dirección elegida: {}'.format(direction))
        print('-' * 44)

        for row in (board or '').splitlines():
            row = row.strip()
            if row:
                print(render_row(row, side, enemy, expected_digit))

        print('=' * 44)
    except Exception as e:
        print('(no se pudo dibujar el tablero: {})'.format(e))


# ---------------------------------------------------------------------------
# Comunicación con el servidor
# ---------------------------------------------------------------------------

async def send(websocket, action, data):
    message = json.dumps(
        {
            'action': action,
            'data': data,
        }
    )
    print(message)
    await websocket.send(message)


async def send_move(websocket, turn, action_type, direction):
    """Manda una acción de jugada y la deja registrada en el log de la partida."""
    move_data = {
        'game_id': turn['game_id'],
        'turn_token': turn['turn_token'],
        'direction': direction,
    }
    log_action(move_data['game_id'], {'action': action_type, 'data': move_data})
    await send(websocket, action_type, move_data)


async def start(auth_token):
    uri = SERVER_URI.format(auth_token)
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
            print('connection error: {}'.format(e))
            await asyncio.sleep(3)


async def play(websocket):
    while True:
        try:
            request = await websocket.recv()
            print("< {}".format(request))
            request_data = json.loads(request)
            event = request_data.get('event')

            if event == 'game_over':
                game_id = request_data['data'].get('game_id')
                if game_id:
                    log_event(game_id, request_data)
                    write_game_log(game_id)

            elif event == 'challenge':
                await send(
                    websocket,
                    'accept_challenge',
                    {
                        'challenge_id': request_data['data']['challenge_id'],
                    },
                )

            elif event == 'your_turn':
                log_event(request_data['data']['game_id'], request_data)
                await process_your_turn(websocket, request_data)

        except KeyboardInterrupt:
            print('Exiting...')
            break
        except Exception as e:
            print('error {}'.format(str(e)))
            break  # se fuerza una nueva conexión


# ---------------------------------------------------------------------------
# Decisión de la jugada
# ---------------------------------------------------------------------------

async def process_your_turn(websocket, request_data):
    """Nunca deja un turno sin respuesta: si algo falla, manda la jugada de respaldo."""
    turn = request_data['data']
    try:
        await process_move(websocket, request_data)
    except Exception as e:
        print('error al decidir, mando jugada de respaldo: {}'.format(e))
        await send_move(websocket, turn, 'move', FALLBACK_DIRECTION)


async def process_move(websocket, request_data):
    turn_data = request_data['data']
    side = turn_data.get('side', 'A')

    dibujar_tablero(
        board=turn_data.get('board'),
        side=side,
        score=turn_data.get('score_1' if side == 'A' else 'score_2'),
        remaining_moves=turn_data.get('remaining_moves'),
        direction=turn_data.get('direction'),
        expected_digit=rules.EXPECTED_DIGIT.get(turn_data.get('game_id')),
        multiplier=turn_data.get('multiplier_1' if side == 'A' else 'multiplier_2', 1),
    )

    raw_direction = rules.choose_direction(turn_data)

    if raw_direction and raw_direction.startswith('shoot_'):
        action_type = 'shoot'
        final_direction = raw_direction[len('shoot_'):]
    else:
        action_type = 'move'
        final_direction = raw_direction or FALLBACK_DIRECTION

    print('\n  -> enviando {}: {}'.format(action_type, final_direction))
    await send_move(websocket, turn_data, action_type, final_direction)


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def main(argv):
    if len(argv) >= 2:
        asyncio.run(start(argv[1]))
        return 0
    print('please provide your auth_token')
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
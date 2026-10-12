import asyncio
import json
import sys
import time
import websockets
import rules

SERVER_URI = "wss://server.codechallenge.net.ar/ws?token={}"

# A running text log of events received / actions sent per game, written to
# game_<game_id>.log when the match ends.
HISTORY = {}


def log_event(game_id, message):
    HISTORY.setdefault(game_id, []).append('< ' + json.dumps(message))


def log_action(game_id, message):
    HISTORY.setdefault(game_id, []).append('> ' + json.dumps(message))


def write_game_log(game_id):
    try:
        with open("game_{}.log".format(game_id), "w") as f:
            f.write("\n".join(HISTORY.get(game_id, [])) + "\n")
        print("saved game_{}.log".format(game_id))
    except OSError as e:
        print("could not write game log: {}".format(e))


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
        except Exception:
            print('connection error!')
            time.sleep(3)


async def play(websocket):
    while True:
        try:
            request = await websocket.recv()
            print("< {}".format(request))
            request_data = json.loads(request)
            if request_data['event'] == 'update_user_list':
                pass
            if request_data['event'] == 'game_over':
                game_id = request_data['data'].get('game_id')
                if game_id:
                    log_event(game_id, request_data)
                    write_game_log(game_id)
            if request_data['event'] == 'challenge':
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
    await process_move(websocket, request_data)


async def process_move(websocket, request_data):
    turn_data = request_data['data']
    
    # 1. Determina tu lado ("A" o "B")
    side = turn_data.get('side', 'A') 
    
    # 2. Llama a tu función de dibujo en lugar del print feo
    dibujar_tablero(
        board=turn_data.get('board'),
        side=side,
        score=turn_data.get(f'score_{"1" if side == "A" else "2"}'),
        remaining_moves=turn_data.get('remaining_moves'),
        direction=turn_data.get('direction'),
        # Nota: expected_digit viene del archivo rules, así que lo obtenemos de allí
        expected_digit=rules.EXPECTED_DIGIT.get(turn_data.get('game_id')),
        multiplier=turn_data.get(f'multiplier_{"1" if side == "A" else "2"}', 1)
    )

    move = {
        'game_id': turn_data['game_id'],
        'turn_token': turn_data['turn_token'],
        'direction': rules.choose_direction(turn_data),
    }
    
    # Imprime la dirección que va a mandar el bot debajo del tablero
    print(f"\n  ➜ Enviando movimiento al servidor: {move['direction']}")
    
    log_action(move['game_id'], {'action': 'move', 'data': move})
    await send(websocket, 'move', move)


def main(argv):
    if len(argv) >= 2:
        asyncio.run(start(argv[1]))
        return 0
    print('please provide your auth_token')
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))

import os

def clear_terminal():
    """Limpia la terminal según el sistema operativo."""
    os.system('cls' if os.name == 'nt' else 'clear')

def dibujar_tablero(board, side, score=None, remaining_moves=None, direction=None, expected_digit=None, multiplier=None):
    """Dibuja el estado actual de Snake, adaptado para nuevas reglas."""
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
    if multiplier is not None:
        print(f"║  ✖️  Multiplicador: x{str(multiplier):<17}║")

    print("╠══════════════════════════════════════════╣")

    # Dibujar tablero
    for row in board.splitlines():
        row = row.strip()
        if not row:
            continue

        visual_row = ""
        for cell in row:
            # Mi serpiente
            if cell == side:
                visual_row += "🟢"
            elif cell == side.lower():
                visual_row += "🟩"
            # Rival
            elif cell == enemy:
                visual_row += "🔴"
            elif cell == enemy.lower():
                visual_row += "🟥"
            
            # Reglas Clásicas y v3/v4/v5
            elif cell == "*":
                visual_row += "🍎"
            elif cell in ("X", "x"):
                visual_row += "✖️"
            elif cell == "#":
                visual_row += "🧱"
            elif cell.isdigit():
                if expected_digit and cell == str(expected_digit):
                    visual_row += "🎯"
                else:
                    visual_row += "⚠️"
            elif cell == "|":
                visual_row += "│"
            elif cell == " ":
                visual_row += "  "
                
            # --- SOPORTE PARA NUEVAS REGLAS ---
            # Si el juego introduce letras nuevas (como 'S' de Shield, 'G' de Golden, etc.)
            elif cell in ("S", "s"):  # Ejemplo: Escudo
                visual_row += "🛡️"
            elif cell in ("G", "g"):  # Ejemplo: Manzana Dorada
                visual_row += "🟡"
            elif cell in ("R", "r"):  # Ejemplo: Revertir controles
                visual_row += "🔄"
            else:
                # Si aparece un símbolo o letra que el bot aún no conoce,
                # lo imprime tal cual para que lo veas y no se rompa la interfaz.
                visual_row += f"{cell} " 

        print(f"║ {visual_row:<36}║")
        
    print("╠══════════════════════════════════════════╣")
    print("║  🟢 Cabeza   🟩 Cuerpo  🍎 Comida        ║")
    print("║  🔴 Rival    🟥 Cuerpo  🧱 Muro móvil    ║")
    print("║  🎯 Dígito ✓ ⚠️  Dígito ✖️ ✖️ Bonus X    ║")
    print("║  NUEVO: 🛡️ Escudo  🟡 Oro  🔄 Reversa   ║")
    print("╚══════════════════════════════════════════╝")
    
    if direction:
        print(f"\n  ➜ El bot eligió: {direction}")
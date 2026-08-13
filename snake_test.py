import asyncio
import websockets
import json

TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiR2FlbCJ9.tpHwnI3fZ0WDYqYN40LI7wZWfcr2233HxHGPYceNPks"

URI = f"wss://server.codechallenge.net.ar/ws?token={TOKEN}"


async def main():
    print("Conectando...")

    async with websockets.connect(URI) as websocket:
        print("¡Conectado!")

        while True:
            message = await websocket.recv()

            print("\n========== MENSAJE ==========")
            print(message)

            try:
                data = json.loads(message)
                print("\nJSON:")
                print(json.dumps(data, indent=2))
            except json.JSONDecodeError:
                print("El mensaje no es JSON.")


asyncio.run(main())
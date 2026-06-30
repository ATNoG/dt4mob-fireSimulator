import signal
import os
import asyncio
import logging

from services.ditto import ditto_client
from pipeline import process_ignition
from settings import config

# Ensure the simulation directory exists
os.makedirs(config.simulations_dir, exist_ok=True)

async def main():
    logging.info("Initializing fire simulation worker...")
    
    shutdown_event = asyncio.Event()

    def handle_shutdown():
        logging.info("Received termination signal. Initiating graceful shutdown...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown)
    
    try:
        await ditto_client.connect("fire", process_ignition)
        logging.info("Fire simulation worker is fully active and listening for new ignitions.")


        await shutdown_event.wait()

    except Exception as e:
        logging.error(f"An unexpected error occurred in the execution loop: {e}", exc_info=True)
    finally:
        logging.info("Cleaning up resources and closing connection...")
        await ditto_client.close()
        logging.info("Worker stopped safely.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Process interrupted by user. Exiting.")
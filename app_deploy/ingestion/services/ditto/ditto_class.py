from typing import Callable
from models.fire_incident import fireIncidentThing
import json
import asyncio
import logging
import ssl

import httpx
from websockets.asyncio.client import connect as ws_connect

from services.auth import AuthenticationService
from settings.ditto import DittoSettings
from .utils import get_headers, prepare_search_params, Action
from urllib.parse import urljoin 


class DittoConnectionError(Exception):
    """Exeption for websocket error"""

    pass


class DittoClient:
    def __init__(
        self,
        client: httpx.Client,
        auth_service: AuthenticationService,
        ditto_settings: DittoSettings,
    ):
        self._ws = None
        self._client = client
        self._auth_service = auth_service
        self._ditto_settings = ditto_settings

        self._search_things_url = ditto_settings.get_base_url() + "/search/things"
        self._things_url = ditto_settings.get_base_url() + "/things"
        self._responses = {}

    def _get_auth_header(self) -> str:
        token = self._auth_service.get_token()
        return f"Bearer {token}"

    async def connect(self, fire_namespace, process_callable: Callable):
        uri = self._ditto_settings.get_base_ws()
        logging.debug(f"URIF:{uri}")
        auth_header = self._get_auth_header()
        
        ssl_context = None
        if uri.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        self._ws = await ws_connect(
            uri, additional_headers={"Authorization": auth_header},ssl=ssl_context
        )
        logging.info(f"Connected to ditto at {uri}")
        
        asyncio.create_task(self.listen_loop(process_callable))

        self._refresh_task = asyncio.create_task(self._token_refresh_loop())
        
        await self.send_control_message(f"START-SEND-EVENTS?namespaces={fire_namespace}&filter=eq(attributes/state,'new_ignition')")

    async def _token_refresh_loop(self):
        """Background task that maintains the WebSocket authorization state."""
        logging.info("Starting background WebSocket token lifecycle supervisor.")
        try:
            while self._ws is not None:
                time_left = self._auth_service.seconds_until_expiration()
                
                sleep_interval = max(5.0, time_left - 5.0) 
                
                logging.debug(f"Token has {time_left:.1f}s left. Supervisor sleeping for {sleep_interval:.1f}s")
                await asyncio.sleep(sleep_interval)
                
                if self._ws is None:
                    break

                logging.info("Token buffer threshold met. Requesting upstream identity update...")
                fresh_token = await asyncio.to_thread(self._auth_service.refresh)
                
                control_payload = f"JWT-TOKEN?jwtToken={fresh_token}"
                
                logging.debug("Sending token extension frame control message payload to Ditto.")
                await self._ws.send(control_payload)
                logging.info("WebSocket authorization extension lease pushed successfully.")
                
        except asyncio.CancelledError:
            logging.debug("Token supervisor loop task cleanly cancelled.")
        except Exception as e:
            logging.error(f"Error encountered in token lifetime supervisor: {e}", exc_info=True)


    async def listen_loop(self,process: Callable):
        if self._ws is None:
            raise DittoConnectionError("No Websocket connection available.")

        try:
            async for message in self._ws:
                logging.debug("Received WebSocket payload: %s", message)
                
                if isinstance(message, str) and message.endswith(":ACK"):
                    future = self._responses.pop(message, None)
                    if future and not future.done():
                        future.set_result(True)
                    continue

                try:
                    data = json.loads(message)
                except (json.JSONDecodeError, TypeError) as e:
                    logging.warning(f"Received message that is neither a control ACK nor valid JSON: {e}")
                    continue

                corr_id = data.get("headers", {}).get("correlation-id")
                
                if corr_id and corr_id in self._responses:
                    future = self._responses.pop(corr_id, None)
                    if future and not future.done():
                        future.set_result(data)
                    continue
                logging.info(f"Processing streamed event topic: {data.get('topic')}")
                
                coro = asyncio.to_thread(process, data)
                asyncio.create_task(coro)

        except Exception as e:
            logging.error(f"Fatal error in WebSocket listen loop: {e}", exc_info=True)
        finally:
            # Prevent hanging awaits if the loop terminates unexpectedly
            logging.warning("WebSocket listen loop stopped. Cleaning up pending futures.")
            for key, future in self._responses.items():
                if not future.done():
                    future.set_exception(DittoConnectionError("WebSocket disconnected while waiting for response."))
            self._responses.clear()


    async def close(self):
        if self._ws is not None:
            await self._ws.close()

    async def send_control_message(self, command: str, timeout: float = 20.0) -> bool:
        """
        Sends a plaintext control command (e.g., START-SEND-EVENTS) to Eclipse Ditto 
        and waits for its corresponding string ACK.
        """
        if not self._ws:
            raise DittoConnectionError("Websocket is not connected")

        base_command = command.split('?')[0]
        expected_ack = f"{base_command}:ACK"

        ack_future = asyncio.get_running_loop().create_future()
        
        self._responses[expected_ack] = ack_future

        try:
            logging.debug(f"Sending Ditto control command: {command}")
            await self._ws.send(command)
            
            # Wait for the reader loop to intercept the ACK string
            await asyncio.wait_for(ack_future, timeout=timeout)
            logging.info(f"Successfully subscribed via control command: {base_command}")
            return True

        except asyncio.TimeoutError:
            logging.error(f"Timeout waiting for control ACK: {expected_ack}")
            return False
        finally:
            self._responses.pop(expected_ack, None)

    def update_fire_incident(self, incident: fireIncidentThing) -> None:
        """Synchronous update method using standard httpx.Client"""
        url = urljoin(self._things_url, incident.thing_id)

        headers = get_headers(Action.UPDATE)
        headers["Authorization"] = self._get_auth_header()

        body = incident.model_dump(by_alias=True, exclude_none=True, mode="json")

        try:
            resp = self._client.patch(url, json=body, headers=headers)
            if resp.status_code in (200, 204):
                logging.info("[fire_incident] Successfully updated %s (sync)", incident.thing_id)
            else:
                logging.error("[fire_incident] Sync update failed: %s", resp.text)
        except httpx.RequestError as exc:
            logging.error("[fire_incident] Network error in sync update: %s", exc)

    def get_stations_zoom_sync(self, lat: float, lon: float, zoom: int) -> list[dict]:
        """Synchronous search method using standard httpx.Client"""
        params = prepare_search_params(lat, lon, zoom)
        headers = {"Authorization": self._get_auth_header()}
        
        resp = self._client.get(
            self._search_things_url, 
            params=params,
            headers=headers
        )
        resp.raise_for_status()
        
        return resp.json().get("items", [])
        

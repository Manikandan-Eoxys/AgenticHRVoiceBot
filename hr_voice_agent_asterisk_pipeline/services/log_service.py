"""
services/log_service.py

Centralized service for sending application logs
to the external monitoring API.
"""

import logging
import os
from typing import Any, Dict, Optional

# pyrefly: ignore [missing-import]
import aiohttp


logger = logging.getLogger(__name__)


class LogService:
    """
    Sends application logs to the monitoring service.

    Monitoring API:
        POST http://192.168.0.230:8570/api/logs
    """

    def __init__(
        self,
        url: Optional[str] = None,
        service_name: Optional[str] = None,
        timeout: int = 5,
    ):
        self.url = url or os.getenv(
            "MONITORING_LOG_URL",
            "http://192.168.0.230:8570/api/logs",
        )

        self.service_name = service_name or os.getenv(
            "MONITORING_SERVICE_NAME",
            "AgenticHRVoiceBot",
        )

        self.timeout = timeout

    async def send_log(
        self,
        message: str,
        level: str = "INFO",
        service: Optional[str] = None,
    ) -> bool:
        """
        Send a log entry to the monitoring API.

        Example payload:
        {
            "message": "Monitoring test logfgdsds",
            "service": "monitoring-test",
            "level": "INFO"
        }
        """

        payload: Dict[str, Any] = {
            "message": message,
            "service": service or self.service_name,
            "applicationName": service or self.service_name,
            "environment": "development",
            "level": level.upper(),
        }

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)

            async with aiohttp.ClientSession(
                timeout=timeout
            ) as session:

                async with session.post(
                    self.url,
                    json=payload,
                ) as response:
                    
                    print(response)
                    if 200 <= response.status < 300:
                        logger.debug(
                            "[LogService] Log sent successfully: %s",
                            payload,
                        )
                        return True

                    response_text = await response.text()

                    logger.warning(
                        "[LogService] Monitoring API returned "
                        "status=%s response=%s",
                        response.status,
                        response_text,
                    )

                    return False

        except Exception as exc:
            # Monitoring must NEVER break the voice agent.
            logger.warning(
                "[LogService] Failed to send monitoring log: %s",
                exc,
            )
            return False

    async def info(
        self,
        message: str,
        service: Optional[str] = None,
    ) -> bool:
        return await self.send_log(
            message=message,
            level="INFO",
            service=service,
        )

    async def warning(
        self,
        message: str,
        service: Optional[str] = None,
    ) -> bool:
        return await self.send_log(
            message=message,
            level="WARNING",
            service=service,
        )

    async def error(
        self,
        message: str,
        service: Optional[str] = None,
    ) -> bool:
        return await self.send_log(
            message=message,
            level="ERROR",
            service=service,
        )

    async def debug(
        self,
        message: str,
        service: Optional[str] = None,
    ) -> bool:
        return await self.send_log(
            message=message,
            level="DEBUG",
            service=service,
        )


# Global instance
log_service = LogService()
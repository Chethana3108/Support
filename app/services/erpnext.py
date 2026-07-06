import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from app.config import settings

logger = logging.getLogger("biztechbot")

def is_retryable_error(exc: BaseException) -> bool:
    """Determine if HTTP request error is temporary and retryable."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError))

class ERPNextService:
    @staticmethod
    def _get_headers() -> Dict[str, str]:
        return {
            "Authorization": f"token {settings.ERPNEXT_API_KEY}:{settings.ERPNEXT_API_SECRET}",
            "Content-Type": "application/json",
        }

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def get_lead_by_email(cls, email: str) -> Optional[str]:
        """Search for an existing Lead by email address in ERPNext. Returns the lead ID/name if found."""
        if not email:
            return None

        headers = cls._get_headers()
        params = {
            "filters": json.dumps([["email_id", "=", email]])
        }
        
        async with httpx.AsyncClient(timeout=10, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.get(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
                if data:
                    return data[0].get("name")
            except httpx.HTTPStatusError as e:
                logger.error(f"ERPNext query status error {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Error querying ERPNext lead by email: {e}")
                raise
        return None

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def get_lead_by_phone(cls, phone: str) -> Optional[str]:
        """Search for an existing Lead by mobile number in ERPNext. Returns the lead ID/name if found."""
        if not phone:
            return None

        headers = cls._get_headers()
        params = {
            "filters": json.dumps([["mobile_no", "=", phone]])
        }
        
        async with httpx.AsyncClient(timeout=10, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.get(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
                if data:
                    return data[0].get("name")
            except httpx.HTTPStatusError as e:
                logger.error(f"ERPNext query status error {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Error querying ERPNext lead by phone: {e}")
                raise
        return None

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def get_lead_by_name_and_company(cls, lead_name: str, company_name: str) -> Optional[str]:
        """Search for an existing Lead by first_name + company_name in ERPNext. Returns the lead ID/name if found."""
        if not lead_name or not company_name:
            return None

        headers = cls._get_headers()
        params = {
            "filters": json.dumps([
                ["first_name", "=", lead_name],
                ["company_name", "=", company_name]
            ])
        }
        
        async with httpx.AsyncClient(timeout=10, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.get(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
                if data:
                    return data[0].get("name")
            except httpx.HTTPStatusError as e:
                logger.error(f"ERPNext query status error {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Error querying ERPNext lead by name+company: {e}")
                raise
        return None

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def create_lead(cls, lead_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new Lead in ERPNext.
        
        Creates a lead with whatever fields are available. Only first_name is
        truly mandatory for ERPNext; email, phone, and company are optional.
        """
        full_notes = lead_data.get("notes", "")
        formatted_note = ""
        if full_notes:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            formatted_note = f"[{timestamp}] {full_notes}"

        payload = {
            "doctype": "Lead",
            "first_name": lead_data.get("lead_name", ""),
            "email_id": lead_data.get("email", ""),
            "mobile_no": lead_data.get("phone", ""),
            "company_name": lead_data.get("company_name", ""),
            "custom_bot_service": formatted_note,
            "source": "Bot",
        }
        if full_notes:
            payload["notes"] = [{"note": full_notes}]

        # Strip out empty fields so ERPNext doesn't validate empty strings
        payload = {k: v for k, v in payload.items() if v}
        headers = cls._get_headers()

        logger.debug(f"ERPNext create payload: {json.dumps(payload, default=str)}")

        async with httpx.AsyncClient(timeout=15, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.post(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                logger.debug(f"ERPNext lead creation success: {resp.status_code}")
                return {"success": resp.status_code in (200, 201), "detail": resp.text}
            except httpx.HTTPStatusError as e:
                error_body = e.response.text
                status_code = e.response.status_code
                logger.error(
                    f"[ERPNEXT-CREATE] Lead creation FAILED. "
                    f"Status: {status_code}, Response: {error_body}, "
                    f"Payload sent: {json.dumps(payload, default=str)}"
                )
                # For client errors (4xx), return a failure result instead of raising
                # so the caller can decide whether to retry on a future turn
                if 400 <= status_code < 500:
                    return {"success": False, "detail": error_body, "status_code": status_code}
                raise
            except Exception as e:
                logger.error(f"[ERPNEXT-CREATE] Lead creation error: {e}")
                raise

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def update_lead(cls, lead_id: str, lead_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing Lead in ERPNext."""
        full_notes = lead_data.get("notes", "")
        headers = cls._get_headers()

        # Fetch existing lead's custom_bot_service value
        existing_custom_bot_service = ""
        async with httpx.AsyncClient(timeout=10, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.get(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead/{lead_id}",
                    headers=headers,
                )
                if resp.status_code == 200:
                    lead_details = resp.json().get("data", {})
                    existing_custom_bot_service = lead_details.get("custom_bot_service") or ""
            except Exception as e:
                logger.error(f"Error fetching existing lead details for {lead_id} from ERPNext: {e}")

        # Determine the updated custom_bot_service value
        if full_notes:
            clean_note = full_notes.strip()
            # If the new note is already a substring of the existing notes, do not append
            if clean_note and clean_note not in existing_custom_bot_service:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                new_entry = f"[{timestamp}] {full_notes}"
                if existing_custom_bot_service:
                    updated_custom_bot_service = f"{existing_custom_bot_service}\n{new_entry}"
                else:
                    updated_custom_bot_service = new_entry
            else:
                updated_custom_bot_service = existing_custom_bot_service
        else:
            updated_custom_bot_service = existing_custom_bot_service

        payload = {
            "doctype": "Lead",
            "first_name": lead_data.get("lead_name", ""),
            "email_id": lead_data.get("email", ""),
            "mobile_no": lead_data.get("phone", ""),
            "company_name": lead_data.get("company_name", ""),
            "custom_bot_service": updated_custom_bot_service,
            "source": "Bot",
        }
        if full_notes:
            payload["notes"] = [{"note": full_notes}]

        # Strip out empty fields
        payload = {k: v for k, v in payload.items() if v}

        async with httpx.AsyncClient(timeout=15, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.put(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead/{lead_id}",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                logger.debug(f"ERPNext lead update success: {resp.status_code}")
                return {"success": resp.status_code == 200, "detail": resp.text}
            except httpx.HTTPStatusError as e:
                logger.error(f"ERPNext lead update failed with {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"ERPNext lead update error: {e}")
                raise

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_error),
        reraise=True,
    )
    async def delete_lead(cls, lead_id: str) -> Dict[str, Any]:
        """Delete a Lead from ERPNext."""
        headers = cls._get_headers()
        async with httpx.AsyncClient(timeout=15, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.delete(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead/{lead_id}",
                    headers=headers,
                )
                resp.raise_for_status()
                logger.debug(f"ERPNext lead deletion success: {resp.status_code}")
                return {"success": resp.status_code in (200, 202, 204), "detail": resp.text}
            except httpx.HTTPStatusError as e:
                logger.error(f"ERPNext lead deletion failed with {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"ERPNext lead deletion error: {e}")
                raise

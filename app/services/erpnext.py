import json
import logging
from typing import Optional, Dict, Any
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
        # Truncating job title if it exists to fit constraints, keeping the notes logic
        truncated_job_title = full_notes[:135] if full_notes else ""

        payload = {
            "doctype": "Lead",
            "first_name": lead_data.get("lead_name", ""),
            "email_id": lead_data.get("email", ""),
            "mobile_no": lead_data.get("phone", ""),
            "company_name": lead_data.get("company_name", ""),
            "job_title": truncated_job_title,
            "source": "Campaign",
        }
        if full_notes:
            payload["notes"] = [{"note": full_notes}]

        # Strip out empty fields so ERPNext doesn't validate empty strings
        payload = {k: v for k, v in payload.items() if v}
        headers = cls._get_headers()

        logger.info(f"[ERPNEXT-CREATE] Sending payload to ERPNext: {json.dumps(payload, default=str)}")

        async with httpx.AsyncClient(timeout=15, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.post(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                logger.info(f"[ERPNEXT-CREATE] Lead creation success: {resp.status_code}")
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
        truncated_job_title = full_notes[:135] if full_notes else ""

        payload = {
            "first_name": lead_data.get("lead_name", ""),
            "email_id": lead_data.get("email", ""),
            "mobile_no": lead_data.get("phone", ""),
            "company_name": lead_data.get("company_name", ""),
            "job_title": truncated_job_title,
            "source": "Campaign",
        }
        if full_notes:
            payload["notes"] = [{"note": full_notes}]

        # Strip out empty fields
        payload = {k: v for k, v in payload.items() if v}
        headers = cls._get_headers()

        async with httpx.AsyncClient(timeout=15, verify=settings.ERPNEXT_SSL_VERIFY) as client:
            try:
                resp = await client.put(
                    f"{settings.ERPNEXT_URL}/api/resource/Lead/{lead_id}",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                logger.info(f"ERPNext lead update success: {resp.status_code}")
                return {"success": resp.status_code == 200, "detail": resp.text}
            except httpx.HTTPStatusError as e:
                logger.error(f"ERPNext lead update failed with {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"ERPNext lead update error: {e}")
                raise

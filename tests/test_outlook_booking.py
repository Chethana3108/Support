import asyncio
import os
import sys
import datetime
from datetime import timedelta, datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.outlook import OutlookService

async def test_booking():
    print("--- Testing Microsoft Graph Outlook Service ---")
    
    # 1. Test token retrieval
    token = await OutlookService.get_access_token()
    if token:
        print("[SUCCESS] MS Graph Token obtained successfully!")
    else:
        print("[WARNING] MS Graph Token request returned None")

    # 2. Test Rule 1 (24-hour delay) & Rule 2 (Available slots calculation)
    tomorrow = dt.now() + timedelta(days=1)
    date_str = tomorrow.strftime("%Y-%m-%d")
    print(f"\n--- Testing available slots for tomorrow ({date_str}) ---")
    
    result = await OutlookService.get_available_slots(date_str)
    print("Slots result:", result)

    # 3. Test slot calculation for 2 days from now
    day_after = dt.now() + timedelta(days=2)
    date_str_2 = day_after.strftime("%Y-%m-%d")
    print(f"\n--- Testing available slots for {date_str_2} ---")
    result_2 = await OutlookService.get_available_slots(date_str_2)
    print("Slots result:", result_2)

if __name__ == "__main__":
    asyncio.run(test_booking())

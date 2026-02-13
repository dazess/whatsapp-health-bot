import requests
import os
import urllib.parse
from datetime import timedelta

def generate_google_calendar_link(title, start_dt, description=""):
    # Assuming 1 hour duration
    end_dt = start_dt + timedelta(hours=1)
    
    # Format dates as YYYYMMDDTHHMMSS
    fmt = '%Y%m%dT%H%M%S'
    dates = f"{start_dt.strftime(fmt)}/{end_dt.strftime(fmt)}"
    
    base_url = "https://calendar.google.com/calendar/render"
    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": dates,
        "details": description,
    }
    
    return f"{base_url}?{urllib.parse.urlencode(params)}"

class BaileysClient:
    def __init__(self):
        self.base_url = 'http://127.0.0.1:3000' # Local Node.js service
        self.api_key = os.getenv('WA_SERVICE_API_KEY', '').strip()

    def send_message(self, phone_number, message):
        """
        Sends a text message using the local Baileys service.
        """
        url = f"{self.base_url}/send-message"
        
        payload = {
            "phone": phone_number,
            "message": message
        }
        headers = {}
        if self.api_key:
            headers['X-Api-Key'] = self.api_key

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text}

            if response.ok:
                return body

            error_message = body.get('error') if isinstance(body, dict) else str(body)
            print(f"Failed sending to {phone_number}: HTTP {response.status_code} - {error_message}")
            return {
                "status": "error",
                "status_code": response.status_code,
                "error": error_message,
            }
        except requests.exceptions.RequestException as e:
            print(f"Error sending message to {phone_number}: {e}")
            return {
                "status": "error",
                "error": str(e),
            }

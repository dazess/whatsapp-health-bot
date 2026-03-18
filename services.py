import requests
import os
import urllib.parse
from datetime import timedelta

OPENROUTER_API_URL = 'https://openrouter.ai/api/v1/chat/completions'


def generate_birthday_card(patient_name: str, patient_description: str = '') -> str:
    """
    Calls the OpenRouter API to generate a personalised birthday card message.
    Raises an exception with a descriptive message if anything goes wrong.
    """
    api_key = os.getenv('OPENROUTER_API_KEY', '').strip()
    if not api_key:
        raise ValueError('OPENROUTER_API_KEY is not set in environment')

    description_hint = f"\n病人資料：{patient_description}" if patient_description else ""

    prompt = (
        f"你是一位親切的醫療診所職員，需要為病人「{patient_name}」撰寫一張溫馨的 WhatsApp 生日卡。"
        f"{description_hint}\n\n"
        "要求：\n"
        "1. 使用繁體中文書面語\n"
        "2. 語氣溫暖、親切、真誠，對象為小朋友\n"
        "3. 可加入健康與愉快成長的祝福\n"
        "4. 長度約 50 至 80 字\n"
        "5. 只輸出生日卡內容本身，不需標題或解釋。"
    )

    response = requests.post(
        OPENROUTER_API_URL,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'HTTP-Referer': 'https://whatsapp-health-bot',
            'X-Title': 'WhatsApp Health Bot',
        },
        json={
            'model': 'deepseek/deepseek-chat',
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 400,
            'temperature': 0.85,
        },
        timeout=30,
    )
    data = response.json()
    if not response.ok:
        raise RuntimeError(f'OpenRouter {response.status_code}: {data}')
    content = data['choices'][0]['message']['content'] or ''
    # DeepSeek R1 may prefix output with <think>...</think> reasoning blocks — strip them
    import re
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    return content


def _default_birthday_card(patient_name: str) -> str:
    return (
        f"🎂 {patient_name}，生日快樂！\n\n"
        "今天是你的大日子，診所全體同事祝你生日快樂、身體健康、平安喜樂！\n"
        "祝你每天都開心成長，笑容滿滿！🎉🎊"
    )

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

def send_patient_greeting_if_needed(patient, client=None):
    """
    Checks if a patient has been greeted yet. If not, sends an
    introductory message and marks 'greeted' as True in the database.
    """
    if patient.greeted:
        return False
        
    if client is None:
        client = BaileysClient()
        
    greeting = (
        f"您好 {patient.name}，這裡是診所訊息助理。\n\n"
        "我們會向您發送預約提醒、生日卡與問卷通知。\n"
        "如有任何查詢，請聯絡診所職員。"
    )
    
    print(f"Sending first-time greeting to {patient.name} ({patient.phone_number})...")
    client.send_message(patient.phone_number, greeting)
    
    patient.greeted = True
    from models import db
    db.session.commit()
    return True

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


class QualtricsClient:
    """Minimal Qualtrics responses client for PID matching workflows."""

    def __init__(self):
        self.base_url = os.getenv('QUALTRICS_BASE_URL', '').strip().rstrip('/')
        self.api_token = os.getenv('QUALTRICS_API_TOKEN', '').strip()
        self.survey_id = os.getenv('QUALTRICS_SURVEY_ID', '').strip()
        self.pid_field = os.getenv('QUALTRICS_PID_FIELD', 'PID').strip()

    def is_configured(self):
        return bool(self.base_url and self.api_token and self.survey_id)

    def fetch_responses(self, survey_id=None, page_size=100):
        """
        Fetches responses from Qualtrics list-responses endpoint.
        Returns a list of dictionaries with response data.
        """
        if not self.is_configured() and not survey_id:
            raise ValueError('Qualtrics client is not configured. Set QUALTRICS_BASE_URL, QUALTRICS_API_TOKEN, QUALTRICS_SURVEY_ID.')

        target_survey_id = survey_id or self.survey_id
        if not target_survey_id:
            raise ValueError('survey_id is required when QUALTRICS_SURVEY_ID is not configured.')

        url = f"{self.base_url}/API/v3/surveys/{target_survey_id}/responses"
        headers = {'X-API-TOKEN': self.api_token}
        offset = None
        responses = []

        while True:
            params = {'limit': page_size}
            if offset:
                params['offset'] = offset

            resp = requests.get(url, headers=headers, params=params, timeout=30)
            data = resp.json()
            if not resp.ok:
                raise RuntimeError(f"Qualtrics API error {resp.status_code}: {data}")

            result = data.get('result', {})
            elements = result.get('elements', [])
            responses.extend(elements)

            offset = result.get('nextOffset')
            if not offset:
                break

        return responses

    def extract_pid(self, response_item, pid_field_override=None):
        """Extract PID from common Qualtrics response shapes."""
        field_name = (pid_field_override or self.pid_field or 'PID').strip()
        values = response_item.get('values', {}) if isinstance(response_item, dict) else {}

        pid = None
        if isinstance(values, dict):
            pid = values.get(field_name) or values.get(field_name.lower())

        if not pid and isinstance(response_item, dict):
            pid = response_item.get(field_name) or response_item.get(field_name.lower())

        if pid is None:
            return None

        pid = str(pid).strip().upper()
        return pid or None

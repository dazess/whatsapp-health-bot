import requests
import os
import urllib.parse
from datetime import timedelta

OPENROUTER_API_URL = 'https://openrouter.ai/api/v1/chat/completions'


def generate_birthday_card(patient_name: str, patient_description: str = '') -> str:
    """
    Calls the OpenRouter API to generate a personalised Cantonese birthday card message.
    Raises an exception with a descriptive message if anything goes wrong.
    """
    api_key = os.getenv('OPENROUTER_API_KEY', '').strip()
    if not api_key:
        raise ValueError('OPENROUTER_API_KEY is not set in environment')

    description_hint = f"\n病人資料：{patient_description}" if patient_description else ""

    prompt = (
        f"你係一位親切嘅醫療診所職員，需要為病人「{patient_name}」用廣東話口語寫一張溫馨嘅WhatsApp生日卡。"
        f"{description_hint}\n\n"
        "要求：\n"
        "1. 全程使用廣東話口語（唔係書面語）\n"
        "2. 語氣溫暖、親切、真誠，對象為小朋友\n"
        "3. 適當加入生日賀詞，可提及健康（小朋友有食物敏感）、開心等祝願\n"
        "4. 長度適中，大約50字\n"
        "5. 只輸出生日卡內容本身，唔需要任何解釋或標題"
        "6. 包括生日快樂，並非生日大快樂"
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
        "今日係你嘅大日子，我哋診所全體同事祝你生日快樂、身體健康、萬事如意！\n"
        "希望你今日笑口常開，開開心心慶祝呢個特別嘅日子！🎉🎊"
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

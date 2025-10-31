import os, json, requests
from typing import Tuple

class AIClient:
    def __init__(self, settings):
        self.cfg = settings.data

    def ask(self, prompt: str) -> Tuple[bool, str]:
        """Main ask method - delegates to mode-specific handler"""
        mode = self.cfg['ai'].get('mode', 'claude')
        if mode == 'claude':
            return self.ask_claude(prompt)
        return self.ask_ollama(prompt)

    # ---- Claude (Anthropic) ----
    def ask_claude(self, prompt: str) -> Tuple[bool, str]:
        """Ask Claude API with comprehensive error handling"""
        try:
            a = self.cfg['ai']['anthropic']
            api_key = os.environ.get(a.get('api_key_env', 'ANTHROPIC_API_KEY'), '')
            
            # Validate API key
            if not api_key or api_key.strip() == '':
                return (False, "❌ ANTHROPIC_API_KEY nicht gesetzt. Bitte setze die Umgebungsvariable (z.B. via 'setx ANTHROPIC_API_KEY \"sk-ant-...\"')")
            
            # Validate base URL
            base_url = a.get("base_url", "")
            if not base_url:
                return (False, "❌ config.yaml fehlerhaft: ai.anthropic.base_url fehlt")
            
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            
            data = {
                "model": a.get("model", "claude-sonnet-4-20250514"),
                "max_tokens": int(a.get("max_output_tokens", 800)),
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            }
            
            # Make API request with timeout
            try:
                r = requests.post(
                    base_url, 
                    headers=headers, 
                    data=json.dumps(data), 
                    timeout=60
                )
            except requests.exceptions.Timeout:
                return (False, "❌ Claude API: Timeout nach 60s. Bitte versuche es erneut.")
            except requests.exceptions.ConnectionError:
                return (False, "❌ Claude API: Keine Verbindung. Bitte prüfe deine Internetverbindung.")
            except requests.exceptions.RequestException as e:
                return (False, f"❌ Claude API: Netzwerk-Fehler - {str(e)}")
            
            # Handle HTTP errors
            if r.status_code == 401:
                return (False, "❌ Claude API: Ungültiger API-Key. Bitte prüfe ANTHROPIC_API_KEY.")
            elif r.status_code == 429:
                return (False, "❌ Claude API: Rate Limit erreicht. Bitte warte einen Moment und versuche es erneut.")
            elif r.status_code == 500:
                return (False, "❌ Claude API: Server-Fehler. Bitte versuche es später erneut.")
            elif r.status_code != 200:
                error_msg = r.text[:300] if r.text else "Unbekannter Fehler"
                return (False, f"❌ Claude API HTTP {r.status_code}: {error_msg}")
            
            # Parse response
            try:
                j = r.json()
            except json.JSONDecodeError:
                return (False, "❌ Claude API: Ungültige JSON-Response")
            
            # Extract text content
            parts = j.get("content", [])
            text_parts = []
            for p in parts:
                if isinstance(p, dict) and p.get("type") == "text":
                    text_parts.append(p.get("text", ""))
                elif isinstance(p, str):
                    text_parts.append(p)
            
            result = "\n".join(text_parts).strip()
            if not result:
                result = str(j)[:1000] if j else "Leere Antwort"
            
            return (True, result)
            
        except KeyError as e:
            return (False, f"❌ Config-Fehler: '{e}' in config.yaml fehlt")
        except Exception as e:
            return (False, f"❌ Unerwarteter Fehler: {type(e).__name__}: {str(e)}")

    # ---- Ollama (lokal) ----
    def ask_ollama(self, prompt: str) -> Tuple[bool, str]:
        """Ask Ollama API with comprehensive error handling"""
        try:
            o = self.cfg['ai']['ollama']
            base_url = o.get("base_url", "")

            if not base_url:
                return (False, "❌ config.yaml fehlerhaft: ai.ollama.base_url fehlt")

            data = {
                "model": o.get("model", "qwen2.5:7b-instruct"),
                "messages": [
                    {"role": "system", "content": o.get("system_prompt","")},
                    {"role": "user", "content": prompt}
                ],
                "stream": False
            }

            # Make API request with timeout
            try:
                r = requests.post(base_url, json=data, timeout=120)
            except requests.exceptions.ConnectionError:
                return (False, "❌ Ollama: Verbindung fehlgeschlagen. Ist Ollama gestartet? (z.B. 'ollama serve')")
            except requests.exceptions.Timeout:
                return (False, "❌ Ollama: Timeout nach 120s. Modell zu langsam?")
            except requests.exceptions.RequestException as e:
                return (False, f"❌ Ollama: Netzwerk-Fehler - {str(e)}")
            
            # Handle HTTP errors
            if r.status_code == 404:
                model = o.get("model", "unbekannt")
                return (False, f"❌ Ollama: Modell '{model}' nicht gefunden. Bitte installiere es mit: 'ollama pull {model}'")
            elif r.status_code != 200:
                error_msg = r.text[:300] if r.text else "Unbekannter Fehler"
                return (False, f"❌ Ollama HTTP {r.status_code}: {error_msg}")
            
            # Parse response
            try:
                j = r.json()
            except json.JSONDecodeError:
                return (False, "❌ Ollama: Ungültige JSON-Response")
            
            result = j.get("message", {}).get("content", "").strip()
            if not result:
                result = "Leere Antwort"
            
            return (True, result)
            
        except KeyError as e:
            return (False, f"❌ Config-Fehler: '{e}' in config.yaml fehlt")
        except Exception as e:
            return (False, f"❌ Unerwarteter Fehler: {type(e).__name__}: {str(e)}")


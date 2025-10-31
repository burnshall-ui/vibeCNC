import os, subprocess, shutil, tempfile, re
from typing import Tuple
from pathlib import Path

class CamoticsBridge:
    def __init__(self, cfg):
        self.cfg = cfg.data
        self.process = None
        # Feste Temp-Datei für Hot-Reload
        self.tmp_file = os.path.join(tempfile.gettempdir(), "vibe_cnc_live.nc")

    def _sanitize_for_camotics(self, code: str) -> str:
        """Bereinigt Fanuc G-Code für CAMotics (LinuxCNC-Parser)"""
        lines = code.split('\n')
        sanitized = []

        for line in lines:
            # G50 entfernen (Fanuc CSS Limit - CAMotics kennt das nicht)
            if re.search(r'\bG50\b', line, re.IGNORECASE):
                line = re.sub(r'\bG50\s+S\d+', '', line, flags=re.IGNORECASE)

            # G96 durch G97 ersetzen (CSS → RPM Mode)
            line = re.sub(r'\bG96\b', 'G97', line, flags=re.IGNORECASE)

            # Kommentare mit --- bereinigen (CAMotics denkt das sind Minus-Operatoren)
            if '(' in line and ')' in line:
                def clean_comment(match):
                    comment = match.group(1)
                    # Ersetze --- durch = (oder entferne sie)
                    comment = comment.replace('---', '===')
                    return f'({comment})'
                line = re.sub(r'\((.*?)\)', clean_comment, line)

            # Leere Zeilen nach Bereinigung skippen
            if line.strip():
                sanitized.append(line)

        return '\n'.join(sanitized)

    def _validate_exe_path(self, exe_path: str) -> Tuple[bool, str]:
        """Validates if CAMotics executable path is valid"""
        if not exe_path:
            return (False, "config.yaml: paths.camotics_exe ist leer")
        
        # Check if absolute path exists
        if os.path.isabs(exe_path):
            if not os.path.exists(exe_path):
                return (False, f"CAMotics-EXE nicht gefunden: {exe_path}\nBitte prüfe config.yaml → paths.camotics_exe")
            if not os.access(exe_path, os.X_OK):
                return (False, f"CAMotics-EXE ist nicht ausführbar: {exe_path}")
            return (True, exe_path)
        
        # Check if executable is in PATH
        found = shutil.which(exe_path)
        if not found:
            return (False, f"CAMotics-EXE nicht im PATH: {exe_path}\nBitte setze absoluten Pfad in config.yaml → paths.camotics_exe")
        return (True, found)

    def quick_sim(self, code_text: str) -> Tuple[bool, str]:
        """Quick Sim: Schreibt Code in feste Datei und startet/reloaded CAMotics"""
        exe = self.cfg['paths'].get('camotics_exe', 'camotics.exe')

        # Validate executable
        valid, exe_path = self._validate_exe_path(exe)
        if not valid:
            return (False, exe_path)

        try:
            # Create temp directory if needed
            temp_dir = os.path.dirname(self.tmp_file)
            os.makedirs(temp_dir, exist_ok=True)

            # Bereinige Code für CAMotics (Fanuc → LinuxCNC)
            sanitized_code = self._sanitize_for_camotics(code_text)

            # Write to temp file
            try:
                with open(self.tmp_file, "w", encoding="utf-8") as f:
                    f.write(sanitized_code)
            except IOError as e:
                return (False, f"Fehler beim Schreiben der Temp-Datei: {e}")
            
            # Check if CAMotics is still running
            if self.process:
                try:
                    poll_result = self.process.poll()
                    if poll_result is None:
                        # Process is still running - terminate it
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            # Force kill if terminate didn't work
                            self.process.kill()
                            self.process.wait()
                except (OSError, subprocess.SubprocessError) as e:
                    return (False, f"Fehler beim Beenden von CAMotics: {e}")
            
            # Start CAMotics
            try:
                self.process = subprocess.Popen(
                    [exe_path, self.tmp_file], 
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except FileNotFoundError:
                return (False, f"CAMotics-EXE nicht gefunden: {exe_path}")
            except PermissionError:
                return (False, f"Keine Berechtigung zum Ausführen: {exe_path}")
            except OSError as e:
                return (False, f"OS-Fehler beim Starten von CAMotics: {e}")
            
            return (True, f"CAMotics gestartet (Code bereinigt für LinuxCNC-Parser)")
            
        except Exception as e:
            return (False, f"Unerwarteter Fehler: {type(e).__name__}: {str(e)}")

    def launch(self, nc_path: str) -> Tuple[bool, str]:
        """Legacy-Methode für SEND 2 SIM Button (mit Dialog)"""
        # Validate file exists
        if not os.path.exists(nc_path):
            return (False, f"Datei nicht gefunden: {nc_path}")
        
        exe = self.cfg['paths'].get('camotics_exe', 'camotics.exe')
        
        # Validate executable
        valid, exe_path = self._validate_exe_path(exe)
        if not valid:
            return (False, exe_path)
        
        try:
            subprocess.Popen(
                [exe_path, nc_path], 
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return (True, exe_path)
        except FileNotFoundError:
            return (False, f"CAMotics-EXE nicht gefunden: {exe_path}")
        except PermissionError:
            return (False, f"Keine Berechtigung zum Ausführen: {exe_path}")
        except Exception as e:
            return (False, f"Startfehler: {type(e).__name__}: {str(e)}")

    def copy_to_share(self, nc_path: str) -> Tuple[bool, str]:
        """Kopiert NC-Datei ins VM-Share (LinuxCNC)"""
        # Validate file exists
        if not os.path.exists(nc_path):
            return (False, f"Quelldatei nicht gefunden: {nc_path}")
        
        share = self.cfg['paths'].get('sim_share', '')
        if not share:
            return (False, "Kein VM-Share konfiguriert. Bitte setze paths.sim_share in config.yaml")
        
        if not os.path.exists(share):
            return (False, f"VM-Share nicht erreichbar: {share}\nBitte prüfe die Netzwerkverbindung zur LinuxCNC-VM")
        
        try:
            dest = os.path.join(share, os.path.basename(nc_path))
            shutil.copy2(nc_path, dest)
            return (True, dest)
        except PermissionError:
            return (False, f"Keine Berechtigung zum Schreiben: {share}")
        except OSError as e:
            return (False, f"Datei-Kopierfehler: {e}")
        except Exception as e:
            return (False, f"Kopierfehler: {type(e).__name__}: {str(e)}")
    
    def save_and_copy_to_vm(self, code_text: str, filename: str = "live_test.nc") -> Tuple[bool, str]:
        """Save + Copy to VM: Speichert Code und kopiert ins VM-Share"""
        share = self.cfg['paths'].get('sim_share', '')
        if not share:
            return (False, "Kein VM-Share konfiguriert. Bitte setze paths.sim_share in config.yaml")
        
        if not os.path.exists(share):
            return (False, f"VM-Share nicht erreichbar: {share}\nBitte prüfe die Netzwerkverbindung zur LinuxCNC-VM")
        
        try:
            dest = os.path.join(share, filename)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(code_text)
            return (True, dest)
        except PermissionError:
            return (False, f"Keine Berechtigung zum Schreiben: {share}")
        except OSError as e:
            return (False, f"Datei-Schreibfehler: {e}")
        except Exception as e:
            return (False, f"VM-Copy Fehler: {type(e).__name__}: {str(e)}")


import os, subprocess, shutil, tempfile, re
from typing import Tuple
from pathlib import Path

class CamoticsBridge:
    def __init__(self, cfg):
        self.cfg = cfg.data
        self.process = None
        # Fixed temp file for hot-reload
        self.tmp_file = os.path.join(tempfile.gettempdir(), "vibe_cnc_live.nc")

    def _sanitize_for_camotics(self, code: str) -> str:
        """Sanitizes Fanuc G-code for CAMotics (LinuxCNC parser)"""
        lines = code.split('\n')
        sanitized = []

        for line in lines:
            # Remove G50 (Fanuc CSS Limit - CAMotics doesn't know this)
            if re.search(r'\bG50\b', line, re.IGNORECASE):
                line = re.sub(r'\bG50\s+S\d+', '', line, flags=re.IGNORECASE)

            # Replace G96 with G97 (CSS → RPM Mode)
            line = re.sub(r'\bG96\b', 'G97', line, flags=re.IGNORECASE)

            # Clean comments with --- (CAMotics thinks these are minus operators)
            if '(' in line and ')' in line:
                def clean_comment(match):
                    comment = match.group(1)
                    # Replace --- with = (or remove them)
                    comment = comment.replace('---', '===')
                    return f'({comment})'
                line = re.sub(r'\((.*?)\)', clean_comment, line)

            # Skip empty lines after sanitization
            if line.strip():
                sanitized.append(line)

        return '\n'.join(sanitized)

    def _validate_exe_path(self, exe_path: str) -> Tuple[bool, str]:
        """Validates if CAMotics executable path is valid"""
        if not exe_path:
            return (False, "config.yaml: paths.camotics_exe is empty")
        
        # Check if absolute path exists
        if os.path.isabs(exe_path):
            if not os.path.exists(exe_path):
                return (False, f"CAMotics EXE not found: {exe_path}\nPlease check config.yaml → paths.camotics_exe")
            if not os.access(exe_path, os.X_OK):
                return (False, f"CAMotics EXE is not executable: {exe_path}")
            return (True, exe_path)
        
        # Check if executable is in PATH
        found = shutil.which(exe_path)
        if not found:
            return (False, f"CAMotics EXE not in PATH: {exe_path}\nPlease set absolute path in config.yaml → paths.camotics_exe")
        return (True, found)

    def quick_sim(self, code_text: str) -> Tuple[bool, str]:
        """Quick Sim: Writes code to fixed file and starts/reloads CAMotics"""
        exe = self.cfg['paths'].get('camotics_exe', 'camotics.exe')

        # Validate executable
        valid, exe_path = self._validate_exe_path(exe)
        if not valid:
            return (False, exe_path)

        try:
            # Create temp directory if needed
            temp_dir = os.path.dirname(self.tmp_file)
            os.makedirs(temp_dir, exist_ok=True)

            # Sanitize code for CAMotics (Fanuc → LinuxCNC)
            sanitized_code = self._sanitize_for_camotics(code_text)

            # Write to temp file
            try:
                with open(self.tmp_file, "w", encoding="utf-8") as f:
                    f.write(sanitized_code)
            except IOError as e:
                return (False, f"Error writing temp file: {e}")
            
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
                    return (False, f"Error terminating CAMotics: {e}")
            
            # Start CAMotics
            try:
                self.process = subprocess.Popen(
                    [exe_path, self.tmp_file], 
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except FileNotFoundError:
                return (False, f"CAMotics EXE not found: {exe_path}")
            except PermissionError:
                return (False, f"No permission to execute: {exe_path}")
            except OSError as e:
                return (False, f"OS error starting CAMotics: {e}")
            
            return (True, f"CAMotics started (code sanitized for LinuxCNC parser)")
            
        except Exception as e:
            return (False, f"Unexpected error: {type(e).__name__}: {str(e)}")

    def launch(self, nc_path: str) -> Tuple[bool, str]:
        if not os.path.exists(nc_path):
            return (False, f"File not found: {nc_path}")
        
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
            return (False, f"CAMotics EXE not found: {exe_path}")
        except PermissionError:
            return (False, f"No permission to execute: {exe_path}")
        except Exception as e:
            return (False, f"Start error: {type(e).__name__}: {str(e)}")

    def copy_to_share(self, nc_path: str) -> Tuple[bool, str]:
        if not os.path.exists(nc_path):
            return (False, f"Source file not found: {nc_path}")
        
        share = self.cfg['paths'].get('sim_share', '')
        if not share:
            return (False, "No VM share configured. Please set paths.sim_share in config.yaml")
        
        if not os.path.exists(share):
            return (False, f"VM share not reachable: {share}\nPlease check network connection to LinuxCNC VM")
        
        try:
            dest = os.path.join(share, os.path.basename(nc_path))
            shutil.copy2(nc_path, dest)
            return (True, dest)
        except PermissionError:
            return (False, f"No write permission: {share}")
        except OSError as e:
            return (False, f"File copy error: {e}")
        except Exception as e:
            return (False, f"Copy error: {type(e).__name__}: {str(e)}")
    
    def save_and_copy_to_vm(self, code_text: str, filename: str = "live_test.nc") -> Tuple[bool, str]:
        share = self.cfg['paths'].get('sim_share', '')
        if not share:
            return (False, "No VM share configured. Please set paths.sim_share in config.yaml")
        
        if not os.path.exists(share):
            return (False, f"VM share not reachable: {share}\nPlease check network connection to LinuxCNC VM")
        
        try:
            dest = os.path.join(share, filename)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(code_text)
            return (True, dest)
        except PermissionError:
            return (False, f"No write permission: {share}")
        except OSError as e:
            return (False, f"File write error: {e}")
        except Exception as e:
            return (False, f"VM Copy error: {type(e).__name__}: {str(e)}")


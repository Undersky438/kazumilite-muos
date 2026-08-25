"""MPV playback lifecycle and runtime diagnostics."""

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import traceback

from sdl2 import *

from backend import USER_AGENT
from config import DIAG_PATH, MPV_LOG_PATH


class PlayerMixin:
    def find_mpv(self):
        candidates = [
            shutil.which("mpv"),
            "/usr/bin/mpv",
            "/opt/system/Tools/PortMaster/libs/mpv",
            "/opt/tools/PortMaster/libs/mpv",
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    @staticmethod
    def mpv_request(path, command, expect_reply=False):
        if not path or not os.path.exists(path):
            return None
        client = None
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(0.35)
            client.connect(path)
            client.sendall((json.dumps({"command": command}) + "\n").encode("utf-8"))
            if not expect_reply:
                return True
            chunks = []
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
            response = json.loads(
                b"".join(chunks).split(b"\n", 1)[0].decode("utf-8")
            )
            return response.get("data") if response.get("error") == "success" else None
        except Exception:
            return None
        finally:
            if client:
                client.close()

    def player_action_allowed(self, actions, name, window=0.12):
        now = time.monotonic()
        if now - actions.get(name, 0.0) < window:
            return False
        actions[name] = now
        return True

    def run_mpv(self, mpv, url, episode, resume, quality):
        ipc_path = f"/tmp/kazumilite-mpv-{os.getpid()}.sock"
        try:
            if os.path.exists(ipc_path):
                os.remove(ipc_path)
        except OSError:
            pass
        command = [
            mpv,
            "--fs",
            "--no-terminal",
            "--keep-open=no",
            "--cache=yes",
            "--demuxer-max-bytes=32M",
            "--vd-lavc-threads=4",
            "--tls-verify=no",
            "--referrer=https://next.xifanacg.com/",
            f"--user-agent={USER_AGENT}",
            f"--input-ipc-server={ipc_path}",
            f"--log-file={MPV_LOG_PATH}",
            "--osd-level=1",
            "--osd-on-seek=bar",
        ]
        if resume >= 15:
            command.append(f"--start={resume:.1f}")
        command.append(url)
        print(
            f"[playback] episode={episode['id']} quality={quality} resume={resume:.1f}",
            flush=True,
        )
        process = None
        position = resume
        duration = 0.0
        user_quit = False
        started = time.monotonic()
        last_query = 0.0
        actions = {}
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
            event = SDL_Event()
            while process.poll() is None:
                while SDL_PollEvent(event):
                    if event.type == SDL_QUIT:
                        user_quit = True
                        process.terminate()
                    elif event.type == SDL_CONTROLLERBUTTONDOWN:
                        button = event.cbutton.button
                        if button == SDL_CONTROLLER_BUTTON_B:
                            user_quit = True
                            if not self.mpv_request(ipc_path, ["quit"]):
                                process.terminate()
                        elif button in (
                            SDL_CONTROLLER_BUTTON_A,
                            SDL_CONTROLLER_BUTTON_Y,
                        ):
                            if self.player_action_allowed(actions, "pause", 0.2):
                                self.mpv_request(ipc_path, ["cycle", "pause"])
                        elif button in (
                            SDL_CONTROLLER_BUTTON_DPAD_LEFT,
                            SDL_CONTROLLER_BUTTON_LEFTSHOULDER,
                        ):
                            if self.player_action_allowed(actions, "left"):
                                self.mpv_request(ipc_path, ["seek", -10, "relative"])
                        elif button in (
                            SDL_CONTROLLER_BUTTON_DPAD_RIGHT,
                            SDL_CONTROLLER_BUTTON_RIGHTSHOULDER,
                        ):
                            if self.player_action_allowed(actions, "right"):
                                self.mpv_request(ipc_path, ["seek", 10, "relative"])
                    elif event.type == SDL_JOYHATMOTION:
                        if event.jhat.value & SDL_HAT_LEFT:
                            if self.player_action_allowed(actions, "left"):
                                self.mpv_request(ipc_path, ["seek", -10, "relative"])
                        elif event.jhat.value & SDL_HAT_RIGHT:
                            if self.player_action_allowed(actions, "right"):
                                self.mpv_request(ipc_path, ["seek", 10, "relative"])
                    elif event.type == SDL_KEYDOWN:
                        key = event.key.keysym.sym
                        if key in (SDLK_ESCAPE, SDLK_x):
                            user_quit = True
                            if not self.mpv_request(ipc_path, ["quit"]):
                                process.terminate()
                        elif key in (SDLK_SPACE, SDLK_z):
                            self.mpv_request(ipc_path, ["cycle", "pause"])
                        elif key == SDLK_LEFT:
                            self.mpv_request(ipc_path, ["seek", -10, "relative"])
                        elif key == SDLK_RIGHT:
                            self.mpv_request(ipc_path, ["seek", 10, "relative"])
                now = time.monotonic()
                if now - last_query >= 1.0:
                    current = self.mpv_request(
                        ipc_path, ["get_property", "time-pos"], True
                    )
                    total = self.mpv_request(
                        ipc_path, ["get_property", "duration"], True
                    )
                    if isinstance(current, (int, float)):
                        position = float(current)
                    if isinstance(total, (int, float)):
                        duration = float(total)
                    last_query = now
                SDL_Delay(25)
            code = process.returncode
        except Exception:
            traceback.print_exc()
            code = -1
        finally:
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    process.kill()
            try:
                if os.path.exists(ipc_path):
                    os.remove(ipc_path)
            except OSError:
                pass
        return {
            "code": code,
            "position": position,
            "duration": duration,
            "elapsed": time.monotonic() - started,
            "user_quit": user_quit,
        }

    def launch_mpv(self, playback, episode):
        mpv = self.find_mpv()
        if not mpv:
            self.status = "没有找到 MPV；请打开设置里的环境检查。"
            self.status_kind = "bad"
            return
        anime = self.detail["anime"]
        resume = self.store.playback_position(anime["id"], episode["id"])
        try:
            if os.path.exists(MPV_LOG_PATH):
                os.remove(MPV_LOG_PATH)
        except OSError:
            pass
        self.status = f"正在播放 {episode['label']}（{playback['quality']}）"
        self.status_kind = "good"
        self.render()
        SDL_Delay(80)
        # SDL and MPV both use the DRM/KMS display on muOS. Fully release the
        # SDL video subsystem so MPV cannot leave its buffers in SDL's swapchain.
        self.release_video_output()
        try:
            result = self.run_mpv(
                mpv, playback["url"], episode, resume, playback["quality"]
            )
            should_fallback = (
                playback.get("fallback_url")
                and not result["user_quit"]
                and (
                    result["code"] != 0
                    or (result["position"] < 1 and result["elapsed"] < 10)
                )
            )
            if should_fallback:
                print("[playback] HLS failed; trying MP4 fallback", flush=True)
                result = self.run_mpv(
                    mpv,
                    playback["fallback_url"],
                    episode,
                    resume,
                    "MP4 备用",
                )
            if result["position"] > 0.5:
                try:
                    self.store.record_playback(
                        anime["id"],
                        anime.get("title") or "未命名",
                        episode["id"],
                        episode["label"],
                        result["position"],
                        result["duration"],
                    )
                except OSError as exc:
                    print(f"History save failed: {exc}", flush=True)
            if result["user_quit"] or result["code"] == 0:
                self.status = f"已返回：{episode['label']}"
                self.status_kind = "good"
            else:
                self.status = (
                    f"播放失败，MPV 错误码 {result['code']}；请查看 mpv.log。"
                )
        finally:
            self.restore_video_output()
        self.input_blocked_until = time.monotonic() + 0.45

    def collect_diagnostics(self):
        mpv = self.find_mpv()
        control_dir = os.environ.get("KAZUMI_LITE_CONTROL_DIR", "")
        network = []
        for host in ("api.kazumi.fyi", "rzmsnqblptbceicadbyd.supabase.co"):
            try:
                socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                network.append((host, True))
            except Exception:
                network.append((host, False))
        lines = [
            f"✓ 屏幕：{self.width} × {self.height}",
            f"✓ Python：{platform.python_version()} / {platform.machine()}",
            f"✓ SDL2：{SDL_MAJOR_VERSION}.{SDL_MINOR_VERSION}.{SDL_PATCHLEVEL}",
            ("✓ MPV 可用" if mpv else "✕ 未找到 MPV"),
            ("✓ PortMaster 运行时" if control_dir else "✕ 未找到 PortMaster"),
            (
                "✓ RG35XX Pro 按键已识别"
                if (self.controller or self.joystick)
                else "✕ 未识别掌机按键"
            ),
            (
                "✓ 番剧接口域名可解析"
                if all(ok for _, ok in network)
                else "✕ 番剧接口域名解析失败"
            ),
        ]
        payload = [
            "Kazumi Lite diagnostics",
            f"time={time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"platform={platform.platform()}",
            f"python={sys.version}",
            f"screen={self.width}x{self.height}",
            f"mpv={mpv or 'missing'}",
            f"portmaster={control_dir or 'missing'}",
            f"controller={'yes' if self.controller else 'no'}",
            f"joystick={'yes' if self.joystick else 'no'}",
        ]
        payload.extend(f"dns_{host}={'ok' if ok else 'failed'}" for host, ok in network)
        return lines, payload

    def diagnostics_loaded(self, result):
        self.diagnostic_lines, payload = result
        try:
            with open(DIAG_PATH, "w", encoding="utf-8") as handle:
                handle.write("\n".join(payload) + "\n")
        except OSError as exc:
            print(f"Diagnostics save failed: {exc}", flush=True)
        print("\n".join(payload), flush=True)
        self.push_page("diagnostics")
        self.status = "检查完成。"
        self.status_kind = (
            "good" if all(line.startswith("✓") for line in self.diagnostic_lines) else "bad"
        )

    def run_diagnostics(self):
        self.start_job(
            "正在检查运行环境……",
            self.collect_diagnostics,
            self.diagnostics_loaded,
        )

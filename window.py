import os
import re
import subprocess
import tempfile
import threading
import time
import xml.sax.saxutils as saxutils

import cv2
import numpy as np
import pygame
from djitellopy import Tello


# ============================================================
# 設定
# ============================================================

WINDOW_WIDTH = 960
WINDOW_HEIGHT = 720

WIFI_VISIBLE_COUNT = 9
WIFI_SCAN_INTERVAL = 2.0
WIFI_CONNECT_TIMEOUT = 15.0
WIFI_CHECK_INTERVAL = 1.0

RC_INTERVAL = 0.05
VIDEO_FRAME_INTERVAL = 0.05
HUD_UPDATE_INTERVAL = 0.2
UI_REBUILD_INTERVAL = 0.25

BASE_SPEED = 40
MAX_SPEED = 100
ACCEL_RATE = 20


class WiFiManager:
    """Windows Wi‑Fi を管理するクラス"""

    def run_netsh(self, args):
        try:
            result = subprocess.run(["netsh"] + args, capture_output=True)
            stdout = result.stdout.decode("cp932", errors="replace")
            stderr = result.stderr.decode("cp932", errors="replace")
            return stdout, stderr, result.returncode
        except Exception as exc:
            print("netsh error:", exc)
            return "", str(exc), -1

    def get_networks(self):
        networks = []

        for attempt in range(3):
            output, _, returncode = self.run_netsh(["wlan", "show", "networks", "mode=bssid"])
            if returncode != 0:
                if attempt < 2:
                    time.sleep(0.8)
                    continue
                return []

            pattern = re.compile(r"^\s*SSID\s+\d+\s*:\s*(.*)$", re.MULTILINE)
            for match in pattern.finditer(output):
                ssid = match.group(1).strip()
                if ssid and ssid not in networks:
                    networks.append(ssid)

            if networks:
                return networks

            time.sleep(1.0)

        return []

    def get_current_ssid(self):
        output, _, returncode = self.run_netsh(["wlan", "show", "interfaces"])
        if returncode != 0:
            return None

        match = re.search(r"^\s*SSID\s*:\s*(.+)$", output, re.MULTILINE)
        if match:
            ssid = match.group(1).strip()
            if ssid:
                return ssid
        return None

    def create_profile(self, ssid):
        safe_ssid = saxutils.escape(ssid)
        profile = f'''<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{safe_ssid}</name>
    <SSIDConfig>
        <SSID>
            <name>{safe_ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>open</authentication>
                <encryption>none</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
        </security>
    </MSM>
</WLANProfile>
'''

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False, encoding="utf-8") as handle:
                handle.write(profile)
                temp_path = handle.name

            output, error, returncode = self.run_netsh([
                "wlan",
                "add",
                "profile",
                f"filename={temp_path}",
                "user=current",
            ])

            if output:
                print(output)
            if error:
                print(error)

            return returncode == 0
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    def connect(self, ssid):
        print()
        print("=" * 50)
        print("Connecting to Wi-Fi:", ssid)
        print("=" * 50)

        self.create_profile(ssid)

        output, error, returncode = self.run_netsh(["wlan", "connect", f"name={ssid}"])
        if output:
            print(output)
        if error:
            print(error)

        if returncode != 0:
            print("Wi-Fi connection request failed.")
            return False

        start_time = time.time()
        while time.time() - start_time < WIFI_CONNECT_TIMEOUT:
            current = self.get_current_ssid()
            print("Waiting for Wi-Fi... current =", current)
            if current == ssid:
                print("Wi-Fi connected!")
                return True
            time.sleep(0.5)

        print("Wi-Fi connection timeout.")
        return False




class StateThread(threading.Thread):
    def __init__(self, tello):
        super().__init__(daemon=True)
        self.tello = tello
        self.running = True
        self.lock = threading.Lock()
        self.battery = 0
        self.altitude = 0
        self.speed_x = 0
        self.speed_y = 0
        self.speed_z = 0

    def stop(self):
        self.running = False

    def get_state(self):
        with self.lock:
            return (
                self.battery,
                self.altitude,
                self.speed_x,
                self.speed_y,
                self.speed_z,
            )

    def run(self):
        while self.running:
            try:
                battery = self.tello.get_battery()
                altitude = self.tello.get_height()
                speed_x = self.tello.get_speed_x()
                speed_y = self.tello.get_speed_y()
                speed_z = self.tello.get_speed_z()

                with self.lock:
                    self.battery = battery
                    self.altitude = altitude
                    self.speed_x = speed_x
                    self.speed_y = speed_y
                    self.speed_z = speed_z
            except Exception as exc:
                print("State error:", exc)

            time.sleep(0.2)


class VideoThread(threading.Thread):
    def __init__(self, tello):
        super().__init__(daemon=True)
        self.tello = tello
        self.running = True
        self.frame_read = tello.get_frame_read()
        self.lock = threading.Lock()
        self.frame = None

    def stop(self):
        self.running = False

    def get_frame(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def run(self):
        while self.running:
            try:
                frame = self.frame_read.frame
                if frame is not None:
                    with self.lock:
                        self.frame = frame.copy()
            except Exception as exc:
                print("Video error:", exc)

            time.sleep(0.05)


class WiFiThread(threading.Thread):
    def __init__(self, wifi_manager):
        super().__init__(daemon=True)
        self.manager = wifi_manager
        self.running = True
        self.lock = threading.Lock()
        self.networks = []
        self.current_ssid = None

    def stop(self):
        self.running = False

    def update(self):
        networks = self.manager.get_networks()
        current_ssid = self.manager.get_current_ssid()

        with self.lock:
            self.networks = networks
            self.current_ssid = current_ssid

    def get_networks(self):
        with self.lock:
            return list(self.networks)

    def get_current_ssid(self):
        with self.lock:
            return self.current_ssid

    def run(self):
        self.update()
        while self.running:
            try:
                self.update()
            except Exception as exc:
                print("Wi‑Fi thread error:", exc)
            time.sleep(2.0)


class RCThread(threading.Thread):
    def __init__(self, tello):
        super().__init__(daemon=True)
        self.tello = tello
        self.running = True
        self.lock = threading.Lock()
        self.command = {
            "left_right": 0,
            "forward_backward": 0,
            "up_down": 0,
            "yaw": 0,
        }

    def stop(self):
        self.running = False

    def set_command(self, left_right, forward_backward, up_down, yaw):
        with self.lock:
            self.command["left_right"] = left_right
            self.command["forward_backward"] = forward_backward
            self.command["up_down"] = up_down
            self.command["yaw"] = yaw

    def get_command(self):
        with self.lock:
            return (
                self.command["left_right"],
                self.command["forward_backward"],
                self.command["up_down"],
                self.command["yaw"],
            )

    def run(self):
        last_send = 0.0
        while self.running:
            now = time.time()
            if now - last_send >= RC_INTERVAL:
                try:
                    left_right, forward_backward, up_down, yaw = self.get_command()
                    self.tello.send_rc_control(left_right, forward_backward, up_down, yaw)
                except Exception as exc:
                    print("RC control error:", exc)
                last_send = now
            time.sleep(0.01)


class TelloApp:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption("Tello Control")
        self.clock = pygame.time.Clock()

        self.font = pygame.font.SysFont("consolas", 24)
        self.small_font = pygame.font.SysFont("consolas", 18)
        self.title_font = pygame.font.SysFont("consolas", 32, bold=True)

        self.wifi_manager = WiFiManager()
        self.wifi_thread = WiFiThread(self.wifi_manager)
        self.state_thread = None
        self.video_thread = None
        self.rc_thread = None
        self.tello = None

    def ensure_wifi_thread_started(self):
        if not self.wifi_thread.is_alive():
            self.wifi_thread = WiFiThread(self.wifi_manager)
            self.wifi_thread.start()

        self.ui_surface = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT))
        self.video_surface = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT))
        self.ui_last_refresh = 0.0
        self.ui_dirty = True

    def draw_text(self, text, x, y, font_obj=None, surface=None):
        if surface is None:
            surface = self.screen
        if font_obj is None:
            font_obj = self.font
        rendered = font_obj.render(text, True, (255, 255, 255))
        surface.blit(rendered, (x, y))

    def _rebuild_wifi_ui(self, networks, selected, scroll, status_message):
        surface = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT))
        surface.fill((20, 20, 20))

        self.draw_text("Wi-Fi Selection", 30, 20, self.title_font, surface)
        self.draw_text("Select a Wi-Fi network", 30, 62, self.small_font, surface)
        self.draw_text(f"{len(networks)} network(s) detected", 30, 87, self.small_font, surface)

        list_top = 125
        row_height = 45
        visible_networks = networks[scroll: scroll + WIFI_VISIBLE_COUNT]

        for i, ssid in enumerate(visible_networks):
            actual_index = scroll + i
            y = list_top + i * row_height
            if actual_index == selected:
                pygame.draw.rect(surface, (60, 90, 130), (20, y - 3, WINDOW_WIDTH - 40, row_height - 2))
                prefix = "> "
            else:
                prefix = "  "
            self.draw_text(prefix + ssid, 35, y, surface=surface)

        if scroll > 0:
            self.draw_text("▲ more", WINDOW_WIDTH - 110, list_top - 25, self.small_font, surface)
        if scroll + WIFI_VISIBLE_COUNT < len(networks):
            self.draw_text("▼ more", WINDOW_WIDTH - 110, list_top + WIFI_VISIBLE_COUNT * row_height, self.small_font, surface)

        if status_message:
            self.draw_text(status_message, 30, WINDOW_HEIGHT - 120, self.small_font, surface)

        controls_y = WINDOW_HEIGHT - 75
        pygame.draw.line(surface, (100, 100, 100), (20, controls_y - 10), (WINDOW_WIDTH - 20, controls_y - 10))
        self.draw_text("↑/↓ Select    Enter Connect    R Refresh    ESC Exit", 30, controls_y, self.small_font, surface)

        return surface

    def _rebuild_control_ui(self, selected_ssid, battery, altitude, speed_x, speed_y, speed_z, button_rect):
        surface = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        surface.fill((0, 0, 0, 0))

        hud = pygame.Surface((WINDOW_WIDTH, 100), pygame.SRCALPHA)
        hud.fill((0, 0, 0, 160))
        surface.blit(hud, (0, 0))

        self.draw_text(f"Wi-Fi: {selected_ssid}", 20, 15, self.small_font, surface)
        self.draw_text(f"Battery: {battery}%", 20, 40, self.small_font, surface)
        self.draw_text(f"Altitude: {altitude} cm", 200, 15, self.small_font, surface)
        self.draw_text(f"Speed X:{speed_x} Y:{speed_y} Z:{speed_z}", 200, 40, self.small_font, surface)
        self.draw_text("ESC: Disconnect", WINDOW_WIDTH - 190, 15, self.small_font, surface)

        pygame.draw.rect(surface, (100, 40, 40), button_rect)
        button_text = self.small_font.render("DISCONNECT", True, (255, 255, 255))
        text_rect = button_text.get_rect(center=button_rect.center)
        surface.blit(button_text, text_rect)

        control_surface = pygame.Surface((WINDOW_WIDTH, 55), pygame.SRCALPHA)
        control_surface.fill((0, 0, 0, 170))
        surface.blit(control_surface, (0, WINDOW_HEIGHT - 55))
        self.draw_text(
            "W/S: Forward/Back   A/D: Roll   ←/→: Yaw   ↑/↓: Altitude   SPACE: Takeoff/Land",
            15,
            WINDOW_HEIGHT - 40,
            self.small_font,
            surface,
        )

        return surface

    def start_threads(self, tello):
        self.tello = tello
        self.state_thread = StateThread(tello)
        self.video_thread = VideoThread(tello)
        self.rc_thread = RCThread(tello)

        self.state_thread.start()
        self.video_thread.start()
        self.rc_thread.start()

    def stop_threads(self):
        if self.state_thread is not None:
            self.state_thread.stop()
        if self.video_thread is not None:
            self.video_thread.stop()
        if self.rc_thread is not None:
            self.rc_thread.stop()

        for thread in (self.state_thread, self.video_thread, self.rc_thread):
            if thread is not None:
                thread.join(timeout=1.0)

    def connect_tello(self):
        try:
            tello = Tello()
            tello.connect()
            print("Tello connected!")
            tello.streamon()
            return tello
        except Exception as exc:
            print("Tello connection failed:", exc)
            return None

    def wifi_selection_screen(self):
        self.ensure_wifi_thread_started()
        networks = self.wifi_thread.get_networks()
        selected = 0
        scroll = 0
        status_message = ""
        last_scan_time = 0.0

        while True:
            now = time.time()
            if now - last_scan_time >= 1.5:
                self.wifi_thread.update()
                networks = self.wifi_thread.get_networks()
                last_scan_time = now

            if not networks:
                status_message = "Scanning for Wi‑Fi..."
            else:
                status_message = ""

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    raise SystemExit

                if event.type != pygame.KEYDOWN:
                    continue

                if event.key == pygame.K_UP:
                    if networks:
                        selected -= 1
                        if selected < 0:
                            selected = len(networks) - 1
                        if selected < scroll:
                            scroll = selected
                        self.ui_dirty = True

                elif event.key == pygame.K_DOWN:
                    if networks:
                        selected += 1
                        if selected >= len(networks):
                            selected = 0
                            scroll = 0
                        if selected >= scroll + 7:
                            scroll = selected - 6
                        scroll = min(scroll, max(0, len(networks) - WIFI_VISIBLE_COUNT))
                        self.ui_dirty = True

                elif event.key == pygame.K_r:
                    self.wifi_thread.update()
                    networks = self.wifi_thread.get_networks()
                    selected = min(selected, len(networks) - 1) if networks else 0
                    scroll = min(scroll, max(0, len(networks) - WIFI_VISIBLE_COUNT))
                    status_message = "Wi‑Fi list refreshed"
                    self.ui_dirty = True

                elif event.key == pygame.K_RETURN:
                    if not networks:
                        continue

                    ssid = networks[selected]
                    status_message = f"Connecting to {ssid}..."
                    self.ui_dirty = True
                    self.screen.fill((20, 20, 20))
                    self.draw_text("Wi-Fi Selection", 30, 20, self.title_font)
                    self.draw_text(f"Connecting to: {ssid}", 30, 70)
                    pygame.display.flip()

                    if self.wifi_manager.connect(ssid):
                        self.screen.fill((20, 20, 20))
                        self.draw_text("Tello Connection", 30, 20, self.title_font)
                        self.draw_text(f"Wi-Fi: {ssid}", 30, 70)
                        self.draw_text("Tello connecting...", 30, 110)
                        pygame.display.flip()

                        tello = self.connect_tello()
                        if tello is not None:
                            return tello, ssid

                        status_message = "Tello connection failed"
                        self.screen.fill((20, 20, 20))
                        self.draw_text("Tello connection failed", 30, 70)
                        self.draw_text("Returning to Wi-Fi selection...", 30, 110, self.small_font)
                        pygame.display.flip()
                        time.sleep(2)

                        self.wifi_thread.update()
                        networks = self.wifi_thread.get_networks()
                        selected = 0
                        scroll = 0
                        status_message = ""
                        self.ui_dirty = True
                    else:
                        status_message = "Wi-Fi connection failed"
                        self.ui_dirty = True

            if self.ui_dirty or now - self.ui_last_refresh >= UI_REBUILD_INTERVAL:
                self.ui_surface = self._rebuild_wifi_ui(networks, selected, scroll, status_message)
                self.ui_last_refresh = now
                self.ui_dirty = False

            self.screen.fill((20, 20, 20))
            self.screen.blit(self.ui_surface, (0, 0))
            pygame.display.flip()
            self.clock.tick(30)

    def control_screen(self, tello, selected_ssid):
        print()
        print("=" * 50)
        print("Tello Control")
        print("Wi-Fi:", selected_ssid)
        print("=" * 50)

        self.start_threads(tello)

        key_start_times = {}
        flying = False
        disconnect_requested = False
        ui_dirty = True
        last_render_time = 0.0
        battery = altitude = speed_x = speed_y = speed_z = 0

        def get_speed(key, now):
            if key not in key_start_times:
                return 0
            duration = now - key_start_times[key]
            speed = BASE_SPEED + duration * ACCEL_RATE
            return int(min(speed, MAX_SPEED))

        while True:
            now = time.time()

            if now % 2.0 < 0.05:
                self.wifi_thread.update()

            current_ssid = self.wifi_thread.get_current_ssid()
            if current_ssid is None or current_ssid != selected_ssid:
                print()
                print("Wi-Fi disconnected or changed.")
                print("Expected:", selected_ssid)
                print("Current :", current_ssid)
                try:
                    tello.streamoff()
                except Exception:
                    pass
                try:
                    tello.end()
                except Exception:
                    pass
                return False

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    disconnect_requested = True

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        disconnect_requested = True
                    elif event.key == pygame.K_SPACE:
                        try:
                            if not flying:
                                tello.takeoff()
                                flying = True
                            else:
                                tello.land()
                                flying = False
                        except Exception as exc:
                            print("Takeoff/Land error:", exc)
                        ui_dirty = True
                    elif event.key == pygame.K_BACKSPACE:
                        try:
                            tello.emergency()
                        except Exception as exc:
                            print("Emergency error:", exc)
                        flying = False
                        ui_dirty = True
                    else:
                        key_start_times[event.key] = now

                elif event.type == pygame.KEYUP:
                    if event.key in key_start_times:
                        del key_start_times[event.key]

            if disconnect_requested:
                print("Disconnect requested.")
                if flying:
                    try:
                        tello.land()
                        time.sleep(2)
                    except Exception as exc:
                        print("Landing error:", exc)
                try:
                    tello.streamoff()
                except Exception:
                    pass
                try:
                    tello.end()
                except Exception:
                    pass
                return True

            keys = pygame.key.get_pressed()
            left_right = 0
            forward_backward = 0
            up_down = 0
            yaw = 0

            if keys[pygame.K_w]:
                forward_backward = get_speed(pygame.K_w, now)
            elif keys[pygame.K_s]:
                forward_backward = -get_speed(pygame.K_s, now)

            if keys[pygame.K_a]:
                left_right = -get_speed(pygame.K_a, now)
            elif keys[pygame.K_d]:
                left_right = get_speed(pygame.K_d, now)

            if keys[pygame.K_UP]:
                up_down = get_speed(pygame.K_UP, now)
            elif keys[pygame.K_DOWN]:
                up_down = -get_speed(pygame.K_DOWN, now)

            if keys[pygame.K_LEFT]:
                yaw = -get_speed(pygame.K_LEFT, now)
            elif keys[pygame.K_RIGHT]:
                yaw = get_speed(pygame.K_RIGHT, now)

            self.rc_thread.set_command(left_right, forward_backward, up_down, yaw)

            frame = self.video_thread.get_frame()
            if frame is not None:
                frame = cv2.resize(frame, (WINDOW_WIDTH, WINDOW_HEIGHT), interpolation=cv2.INTER_NEAREST)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame[:, :, 2] = frame[:, :, 2] // 2
                self.video_surface = pygame.surfarray.make_surface(np.transpose(frame, (1, 0, 2)))
            else:
                self.video_surface.fill((0, 0, 0))

            battery, altitude, speed_x, speed_y, speed_z = self.state_thread.get_state()
            button_rect = pygame.Rect(WINDOW_WIDTH - 190, WINDOW_HEIGHT - 65, 160, 40)

            if ui_dirty or now - last_render_time >= UI_REBUILD_INTERVAL:
                self.ui_surface = self._rebuild_control_ui(
                    selected_ssid,
                    battery,
                    altitude,
                    speed_x,
                    speed_y,
                    speed_z,
                    button_rect,
                )
                ui_dirty = False
                last_render_time = now

            if pygame.mouse.get_pressed()[0]:
                mouse_pos = pygame.mouse.get_pos()
                if button_rect.collidepoint(mouse_pos):
                    disconnect_requested = True

            self.screen.fill((0, 0, 0))
            self.screen.blit(self.video_surface, (0, 0))
            self.screen.blit(self.ui_surface, (0, 0))
            pygame.display.flip()
            self.clock.tick(30)

    def run(self):
        while True:
            result = self.wifi_selection_screen()
            tello, selected_ssid = result
            result = self.control_screen(tello, selected_ssid)
            self.stop_threads()

            if result is False:
                print()
                print("Returning to Wi-Fi selection...")
                time.sleep(1)
            else:
                print()
                print("Disconnected.")
                time.sleep(0.5)


# ============================================================
# 起動
# ============================================================

if __name__ == "__main__":
    try:
        app = TelloApp()
        app.run()
    except KeyboardInterrupt:
        print()
        print("Program terminated.")
    finally:
        if 'app' in locals():
            app.wifi_thread.stop()
        pygame.quit()